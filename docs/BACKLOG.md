# Curio — Working Backlog

Running queue of stuff to fix / build, newest priorities first. Updated as we go.

## In progress / just shipped
- [x] **Mobile control-bar bug** — More/Know feedback buttons blocked YouTube's 2x-speed/fullscreen controls on mobile. Moved to centered right edge (Reels-style). `frontend/components/ReelPlayer.tsx`.
- [x] **feed-prefetch-playback core implementation** — warm-window pre-warm (active ±1), overlay-metadata cache, refresh-on-return, `ReelPlayer` `mode` prop, additive `GET /api/feed/clip/{clip_id}`. Frontend `tsc --noEmit` clean; backend imports clean. Spec: `.kiro/specs/feed-prefetch-playback/`.

## Next up (priority order)
1. **Alternative content streams ("switch the content" feature)** — break the YouTube-only dependency: user uploads + non-YouTube providers. This is the #1 structural scaling ceiling (quota scales with content novelty, not users). Friend feedback independently flagged this. → spec next (requirements-first).
2. **Google sign-in (OAuth)** — friend feedback: "integrate google in ur sign in." Supabase Auth supports Google OAuth; mostly config + a button on the login/upgrade screens. Low effort, real friction reducer.

## feed-prefetch-playback — remaining (optional) tasks
- [x] 2.2 / 2.3 — property tests for `computeWarmWindow` (Properties 1 & 2) — pass (100 runs each)
- [x] 3.2 / 3.3 — property tests for the overlay cache (Properties 3 & 4) — pass
- [x] 4.2 — property test for `isRefreshEligible` (Property 5) — pass
- [x] 8.2 — backend pytest for `/api/feed/clip/{clip_id}` (200 / 404 / 503 / auth / route-ordering) — 10 tests pass
- [ ] 5.4 — `ReelPlayer` mode-behavior component test (Testing Library / jsdom)
- [ ] 6.3 — render-budget + telemetry-isolation component tests
- [ ] 9.2 / 10.4 — refresh-on-return + no-regression integration tests
- [ ] Run `npm run build` (full Next build) once before deploy to confirm production bundle.

## Unrelated bug found while running the suite
- [ ] **`normalize_quality_score` vs its property test disagree on numeric strings.**
  `tests/test_prop_quality_normalization.py` (deep-content-ingestion) intermittently
  fails when Hypothesis draws a parseable numeric string (e.g. `'0'`): the impl in
  `app/services/segment_judge.py` coerces `'0'` → `0.0`, but the test expects any
  non-`int/float` type to default to `0.5`. Decide intent: accept numeric strings
  (fix the test) or reject them (fix the impl). Pre-existing; NOT caused by
  feed-prefetch-playback. Flaky in CI until resolved.

## UI / polish ("UI is so buns" — needs specifics)
- [ ] General visual polish pass — gather concrete examples (the button overlap was one).
- [ ] Consider: 2x speed as a first-class in-app control rather than relying on YouTube's gear menu.

## Product / strategic (not yet specced)
- [ ] Personalization side — friend: "make ppl like/dislike videos on topics." Partly exists (interest/taste vectors); the learned recommender is still research (`.kiro/specs/recommendation-model-research`).
- [ ] Enterprise angle — required-training / sales-training content; "if I can show ppl actually learn." Park until consumer traction.
- [ ] Depth vs. hook tension — "real learners want the deep dive." Lean on the learning-path + quiz depth already built, not just the reels hook.

## Known debts (from ARCHITECTURE.md "Limitations & scaling roadmap")
- [ ] Durable ingestion queue (replace FastAPI BackgroundTasks).
- [ ] Pre-aggregate telemetry rollups; batch impression writes off the hot path.
- [ ] Config-drive the ranker + add experimentation harness.
- [ ] Observability: cold-path latency, quota burn, pipeline success metrics.
