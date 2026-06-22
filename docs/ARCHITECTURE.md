# Curio — Architecture

Curio is a staged retrieval→ranking system, structured like a large-scale
recommender (Netflix/Apple-TV homepage style) but scaled down and made **lazy**:
content is encoded on-demand the first time a topic is requested, then cached so
it's never rebuilt.

Everything in the codebase follows one repeated shape: a **pure decision core**
(no DB, no clock, no globals — exhaustively property-tested with Hypothesis)
wrapped by a **thin, best-effort I/O shell** that never blocks the request path
and, where spend is involved, **fails closed**. Read the core to understand a
behavior; read the shell to understand how it touches the world.

There are two pipelines: an **ingestion** path ("get the video → encode it →
move on") and a **serving** path ("query → retrieve → rank → feed"), plus three
subsystems layered on top: **cold-start seeding**, **deep content ingestion**,
and **engagement telemetry**.

---

## Ingestion pipeline — "get the video → encode it → move on"

```
USER QUERY ──► "teach me binary search"
     │
     ▼
┌──────────────────┐   curriculum_agent.run_curriculum()
│  CURRICULUM AGENT │   LLM parses intent → ordered roadmap of ~3 topics
│  (orchestrator)   │   e.g. [binary-search, big-o-notation, recursion]
└─────────┬─────────┘
          │
          ▼
┌──────────────────┐   topic_resolver.resolve_topic()         ┌────────────────┐
│  TOPIC RESOLVER   │── match by name-embedding (≥ 0.84) ────► │  EXISTING CLIPS │ ✅ reuse, STOP
│  (similar-topic   │   "binary-search-basics" → binary-       │  (cache hit)    │    (no encode)
│   cache)          │    search (already seeded)               └────────────────┘
└─────────┬─────────┘
          │  miss → must build
          ▼
┌──────────────────┐   section_planner.plan_and_store_sections()
│  SECTION PLANNER  │   1 topic → 4 sequenced sections, each with its own
│                   │   search_query: hook → what-is-it → how-it-works → outcomes
└─────────┬─────────┘
          │   per section:
          ▼
╔═══════════ pipeline_agent  (LangGraph DAG = the "encode") ════════════════╗
║                                                                            ║
║  ┌──────────┐   ┌──────────────┐   ┌────────────┐   ┌──────────┐          ║
║  │ _node_   │   │  _node_      │   │  _node_    │   │ _node_   │          ║
║  │ search   │──►│  transcribe  │──►│  segment   │──►│ store    │          ║
║  │          │   │              │   │            │   │          │          ║
║  │ YouTube  │   │ TranscriptAPI│   │ GPT cuts   │   │ insert   │          ║
║  │ (CACHED) │   │  (CACHED     │   │ 2-3 clips +│   │ clips +  │          ║
║  │ 100 units│   │  by video_id)│   │ EMBED 384d │   │ embedding│          ║
║  └──────────┘   └──────────────┘   └────────────┘   └────┬─────┘          ║
║   = candidate    = fetch raw        = ENCODE          = land               ║
║     retrieval      transcript         (segment +        in DB              ║
║                                       vector)                              ║
╚════════════════════════════════════════════════════════════│═════════════╝
   "get video → encode → move on" runs HERE, in a background  │
   task; subsequent requests hit the cache and skip it.        ▼
                                                       ┌──────────────────┐
                                                       │   clips table    │
                                                       │ (Supabase+pgvec) │
                                                       └──────────────────┘
```

### Caching layers (so we encode once, then move on)
- **topic_resolver** — semantically-equivalent queries collapse onto an existing
  seeded topic (name-embedding cosine ≥ 0.84 **and** the topic already has clips).
  No YouTube search, no transcript fetch, no segmentation.
- **youtube_search_cache** — search results keyed by query string. A YouTube
  search costs 100 quota units (10k/day free); re-testing a topic is free.
- **transcript_cache** — transcripts keyed by `video_id`, so the same source
  video is never re-fetched from TranscriptAPI across sections or topics.

### In-flight tracking
`topics.generating_slugs` holds slugs whose pipeline is currently running. The
feed reports a topic as `processing` while its slug is in this set — so the
client keeps polling until **all** sections finish, not just until the first
clip lands. The feed also self-heals: an empty topic with no in-flight pipeline
gets its generation re-triggered on feed load.

---

## Serving pipeline — online, on every feed request

```
FEED REQUEST ──► get_path_feed(session)
     │
     ▼
┌────────────────────┐   _fetch_clips_for_slug()  per topic, sampled across sections
│  RETRIEVAL          │   pulls candidate clips from the DB (feed_retrieval)
└─────────┬──────────┘
          ▼
┌────────────────────┐   feed_scoring._compute_scores()  — the learned ranker
│  MULTI-SIGNAL RANKER│   0.28 hook + 0.23 pop-completion + 0.18 duration-affinity
│  (within-row rank)  │   + 0.13 recency + 0.10 interest + 0.08 semantic (taste)
└─────────┬──────────┘
          ▼
┌────────────────────┐   _interleave_topics() + cross-topic dedup
│  ROW / DIVERSITY    │   orders topics and stops the same clip appearing under
│                     │   multiple topic feeds
└─────────┬──────────┘
          ▼
   List<Clip>  ──►  ReelPlayer (display)   ── serve-time impression capture (telemetry) ─┐
          │                                                                              │
          ▼  user watches / 🔥 / ✓ / skips                                               │
┌────────────────────┐   record_clip_event → personalization._update_interest_vector    │
│  FEEDBACK LOOP      │   updates session- and user-level interest + taste vectors,      │
│  (feedback→vectors) │   which feed back into the ranker above                          │
└────────────────────┘                                                                   │
                          impression_store records WHAT WAS SERVED  ◄─────────────────────┘
                          (not just what was watched) for telemetry
```

### Feedback attribution
`record_clip_event` always personalizes for the authenticated user. Path-feed
events update both **session-level** (`session_embeddings`) and **user-level**
(`user_profiles`) vectors; topic-feed / discover events have no session but still
update the user's profile. Feedback (🔥 `want_more` / ✓ `already_know`) is
persisted on the `clip_events` row, not just applied as a live vector nudge.

---

## Subsystem: cold-start seeding (grow the library ahead of demand)

The serving path is only fast when the topic is already encoded. Cold-start
seeding keeps the library ahead of demand so more queries are cache hits.

```
 grade map ──► backlog_store.init_from_grade_map()  (idempotent fill)
                        │
                        ▼
              ┌──────────────────┐   topic_frontier: what to seed next
              │  TOPIC FRONTIER   │   (priority order, persisted in topic_backlog)
              └─────────┬────────┘
                        ▼
   cron (every 6h) ─► seeding_worker.run_once()  ── drains one paced chunk ──┐
                        │                                                    │
                        ▼                                                    │
              ┌──────────────────┐   quota_pool (pure) + quota_store (shell) │
              │  KEY POOL         │   per-PROJECT 10k/day budget, failover,  │
              │  (multi-project)  │   FAIL CLOSED on persistence outage       │
              └─────────┬────────┘                                           │
                        ▼                                                    │
                 shared ingest_topic ──────────────────────────────────────►┘
                        │                       (same path as live requests)
                        ▼
                 level_filter: tag clips with content_level so the feed can
                 match difficulty to the learner's grade_level
```

- **`quota_pool` / `quota_store`** — quota is tracked per **Google Cloud
  project** (10k units/day), not per key. `YT_PROJECTS` is a comma-separated list
  of `project_id:api_key` pairs; usage is persisted per project per Pacific-day in
  `project_quota_usage` via an atomic, overspend-safe `increment_quota_usage` RPC.
  The charge **persists before** the API call, and an unreadable/unwritable store
  makes a project look fully used (fail closed) so an outage halts spend rather
  than risking overspend.
- **`seeding_worker`** — self-pacing and resumable; a run that finds the budget
  spent stops cleanly having done nothing. Runs as a Render cron on the same
  Docker image as the web service (`render.yaml`).
- **`backlog_store.init_from_grade_map()`** runs at the top of every worker pass
  so the frontier is always (re)filled before it is drained.

---

## Subsystem: deep content ingestion (DECODE → BREAK-DOWN → MAP → JUDGE → ADMIT)

For high-value topics, the shallow "search → segment → store" path is replaced by
a deeper pipeline that reasons about a source video before admitting clips.

```
 source video ─► deep_decoder ──► segment_mapper ──► segment_judge ──► admission_gate ──► clips
                 (DECODE:          (MAP: align       (JUDGE: score      (ADMIT: accept/
                  transcript →      candidate         each segment       reject against
                  structured        segments to       for hook /         budget + quality
                  beats)            learning beats)    coherence)         thresholds)
```

- `ingestion_state` / `ingestion_store` — durable, resumable per-topic progress.
- `ingestion_budget` — bounds spend per run (a property test here caught a real
  `OverflowError` on `inf`, since fixed).
- `ingestion_pipeline.ingest_topic` — the **single shared entrypoint**: both the
  whole-topic `run_pipeline` and the cold-start `Seeding_Worker` route through it,
  so there is exactly one ingestion code path to reason about.

---

## Subsystem: engagement telemetry (what the user actually saw)

Telemetry answers "what is a user actually experiencing?" — not just what they
watched, but **what was served to them** and how it cohered into a journey.

```
 serve ─► build_impressions  ─►  impression_store (best-effort, BackgroundTasks)
            (WHAT WAS SERVED)         │
                                      ▼
 watch ─► clip_events ──────► build_session_journey  ─► /api/analytics/journey/...
                              (reconstruct ordered, per-session/per-user timeline)
                                      │
                                      ▼
                              compute_rollup  ─► /api/analytics/rollup/{dimension}
                              (per-slice engagement: watch_ratio, skip class, origin)
```

- **`telemetry.py` (pure)** — `watch_ratio`, `classify_skip`, `build_impressions`,
  `build_session_journey`, `compute_rollup`, `classify_seeding_origin`.
  `watch_ratio` is shared so the dropoff funnel stays byte-identical to the
  rollups.
- **`impression_store.py` (shell)** — best-effort writes off the request path.
- **Operator access** — `auth.is_operator` / `require_operator` gate cross-user
  reads behind an `OPERATOR_USER_IDS` allowlist (Supabase auth UUIDs / JWT `sub`).
  Empty allowlist = no one can read other users' data.

---

## How this maps to a large-scale recommender

| Curio component | Large-scale analog |
|---|---|
| `curriculum_agent` (roadmap orchestration) | Orchestrator that fans out to candidate carousels |
| `topic_resolver` (reuse-or-build) | Candidate source + cache |
| `pipeline_agent` search → transcribe → segment → store | Candidate retrieval + offline **encode** (embeddings) |
| `deep_*` ingestion (decode→judge→admit) | Content-understanding / quality gating before indexing |
| `seeding_worker` + `topic_frontier` | Offline backfill / candidate generation jobs |
| `feed_retrieval._fetch_clips_for_slug` | Unified retrieval service (browse / history / continue) |
| `feed_scoring._compute_scores` | Within-row (carousel) ranker |
| `_interleave_topics` + dedup | Row ranker / diversity layer |
| `record_clip_event` → interest/taste vectors | Online feedback loop |
| `telemetry` impressions + journeys + rollups | Logging / analytics / experimentation substrate |

### The key difference
In a large-scale system, "get content → encode" is a giant **offline batch job**
that ran long before any query. In Curio it's **lazy and on-demand**: the encode
happens in a background task the first time a topic is requested, then the caches
mean we "move on" and never re-encode it. The cold-start seeding worker is the
small offline batch layer that pulls the most common topics out of the cold path
ahead of time.

---

## Limitations & scaling roadmap

The system is correct and safe, but it has structural ceilings worth naming.

### Latency
- **Cold-path latency dominates.** A first-time topic fans out to YouTube search,
  TranscriptAPI, and per-section LLM calls before any clip lands; the client polls
  `processing` throughout. Seeding hides this only for anticipated topics.
- **Model warmup.** The 384-d sentence-transformer (~80MB) loads on first use; a
  background warmup thread helps, but cold container starts on the Render starter
  plan are slow.

### Scalability
- **YouTube quota is the hard ceiling**, and it scales with *content novelty*, not
  users: ~100 searches/day per project. The multi-project pool raises it linearly,
  but every project is provisioned manually.
- **No durable job queue.** Ingestion runs in FastAPI `BackgroundTasks` inside the
  web process — a dyno restart loses in-flight work, and ingestion can't scale
  horizontally beyond the single cron.
- **One Postgres for everything.** Vectors, relational data, `clip_events`, and
  serve-time impression writes share one Supabase instance; rollups are computed
  on read. Fine now, but impression capture is on the hot path and rollups will
  need pre-aggregation at scale.
- **Variable LLM cost** scales with how often users explore new (uncached) ground.

### Product / correctness
- **Single content source.** Quality depends on YouTube + transcript availability;
  there is no user-upload or alternative-provider path yet.
- **Hardcoded ranker.** Weights are constants with no experimentation harness; the
  learned recommender is still in the research spec.
- **Frontend envelope mismatch.** `api.ts` `getDiscoverFeed` is typed `Clip[]` but
  the endpoint returns `{ clips, processing }`.
- **Test coverage.** Pure cores are exhaustively property-tested; DB-backed
  endpoints have no integration coverage.

### Next bets (priority order)
1. **Alternative content streams** (user uploads + non-YouTube providers) to break
   the quota ceiling — highest leverage.
2. **Durable ingestion queue** with retries, replacing `BackgroundTasks` — fixes
   latency-perception and reliability together.
3. **Wider pre-warming** via the frontier/seeding worker; tighten the resolver to
   raise cache-hit rate.
4. **Pre-aggregate telemetry rollups**; batch impression writes off the hot path.
5. **Config-drive the ranker + experimentation**, then graduate to a learned model.
6. **Observability**: cold-path latency, quota burn, and pipeline success metrics.
