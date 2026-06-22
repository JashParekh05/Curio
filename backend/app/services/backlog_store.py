"""Topic_Frontier persistence: thin, best-effort I/O shell over the pure core.

This module is the I/O shell paired with the pure decision core in
``topic_frontier.py``.  Mirroring the convention of ``discover_seeding.py`` and
the design's "pure decision core, thin I/O shell" principle, it holds ONLY the
Supabase reads/writes against the ``topic_backlog`` table and maps rows to and
from the pure :class:`~app.services.topic_frontier.BacklogItem`.  All ordering,
dedupe, bounding, and status decisions live in ``topic_frontier`` and are
applied by the caller; this module never decides anything.

Every entrypoint is best-effort: failures are logged and swallowed so a seeding
or backlog operation can never block or crash the request path, exactly like
``_seed_topics_bg``.  Readers degrade to an empty list; writers degrade to a
no-op.

ASCII only.

Validates: Requirements 3.1, 3.2, 3.10, 2.7
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.db.supabase import get_client
from app.services.discover_seeding import GRADE_LEVEL_TOPIC_MAP, _GRADE_DIFFICULTY
from app.services.reseed_prioritizer import (
    DEFAULT_MINIMUM_VIEWS,
    DEFAULT_RECUT_THRESHOLD,
    ClipQualitySignal,
    WatchQualitySignal,
    identify_clips_to_reseed,
    identify_topics_to_reseed,
)
from app.services.topic_frontier import (
    MAX_ADJACENT_PER_SEED,
    BacklogItem,
    clamp_priority,
    enqueue,
    enqueue_adjacent,
)

logger = logging.getLogger(__name__)

# The Supabase table backing the Topic_Frontier (see migration_cold_start.sql).
_TABLE = "topic_backlog"

# Defaults used when mapping rows that predate this feature or carry NULLs.
_DEFAULT_LEVEL = "intermediate"
_DEFAULT_PRIORITY = 0.5

# The Content_Levels a spawned item may carry; anything else degrades to the
# default so a malformed LLM "difficulty" can never produce an invalid level.
_VALID_LEVELS = ("beginner", "intermediate", "advanced")


# ---------------------------------------------------------------------------
# Row <-> BacklogItem mapping
# ---------------------------------------------------------------------------

def _row_to_item(row: dict) -> BacklogItem:
    """Map a ``topic_backlog`` row to the pure ``BacklogItem``.

    NULL/missing columns degrade to safe defaults so pre-feature rows still map
    cleanly: ``level`` -> ``intermediate``, ``priority`` -> 0.5 (clamped to the
    [0.0, 1.0] invariant), ``status`` -> ``pending``.
    """
    level = row.get("level") or _DEFAULT_LEVEL
    raw_priority = row.get("priority")
    priority = clamp_priority(float(raw_priority)) if raw_priority is not None else _DEFAULT_PRIORITY
    status = row.get("status") or "pending"
    return BacklogItem(
        topic=row["topic"],
        level=level,
        priority=priority,
        status=status,
    )


def _item_to_row(item: BacklogItem) -> dict:
    """Map a pure ``BacklogItem`` to a ``topic_backlog`` upsert row."""
    return {
        "topic": item.topic,
        "level": item.level,
        "priority": clamp_priority(item.priority),
        "status": item.status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Persistence entrypoints
# ---------------------------------------------------------------------------

def init_from_grade_map() -> None:
    """Seed ``topic_backlog`` from ``GRADE_LEVEL_TOPIC_MAP`` when empty.

    Each grade band's slugs are enqueued at the band's Content_Level (via
    ``_GRADE_DIFFICULTY``), default priority 0.5, status ``pending``.  The seed
    is idempotent: if the table already holds any row the seed is skipped, so
    repeated calls (and process restarts) never duplicate or overwrite an
    evolving backlog.  Best-effort: any failure is logged and swallowed.

    Validates: Requirements 3.2
    """
    try:
        db = get_client()
        existing = db.table(_TABLE).select("topic").limit(1).execute()
        if existing.data:
            # Already seeded -- leave the (possibly grown) backlog untouched.
            return

        rows: list[dict] = []
        seen: set[str] = set()
        for grade_band, category_map in GRADE_LEVEL_TOPIC_MAP.items():
            level = _GRADE_DIFFICULTY.get(grade_band, _DEFAULT_LEVEL)
            for slugs in category_map.values():
                for slug in slugs:
                    if slug in seen:
                        # topic is the primary key; keep the first band's level.
                        continue
                    seen.add(slug)
                    rows.append({
                        "topic": slug,
                        "level": level,
                        "priority": _DEFAULT_PRIORITY,
                        "status": "pending",
                    })

        if rows:
            db.table(_TABLE).upsert(rows, on_conflict="topic").execute()
            logger.info("[backlog] seeded topic_backlog with %d topics", len(rows))
    except Exception as exc:
        logger.warning("[backlog] init_from_grade_map failed: %s", exc)


def load_pending() -> list[BacklogItem]:
    """Load all pending Backlog_Items from ``topic_backlog``.

    Done items are excluded at the query level so the caller only ever sees
    selectable work, and the persisted topic/priority/status survive a restart.
    Best-effort: on any failure an empty list is returned.

    Validates: Requirements 3.1, 2.7
    """
    try:
        db = get_client()
        res = (
            db.table(_TABLE)
            .select("topic, level, priority, status")
            .eq("status", "pending")
            .execute()
        )
        return [_row_to_item(row) for row in (res.data or [])]
    except Exception as exc:
        logger.warning("[backlog] load_pending failed: %s", exc)
        return []


def upsert_items(items: list[BacklogItem]) -> None:
    """Persist a batch of Backlog_Items, keyed by topic.

    Used to write back the results of the pure ``enqueue`` / ``enqueue_adjacent``
    growth: new topics are inserted and existing ones updated in place (topic is
    the primary key).  Best-effort: failures are logged and swallowed.

    Validates: Requirements 3.1
    """
    if not items:
        return
    try:
        db = get_client()
        rows = [_item_to_row(item) for item in items]
        db.table(_TABLE).upsert(rows, on_conflict="topic").execute()
    except Exception as exc:
        logger.warning("[backlog] upsert_items failed for %d items: %s", len(items), exc)


def persist_status(topic: str, status: str) -> None:
    """Persist a single Backlog_Item's status transition (e.g. mark done).

    Mirrors ``topic_frontier.mark_done`` / ``apply_seed_outcome`` on the I/O
    side: a successful seed calls this with ``"done"`` so the item is excluded
    from future selection; a failure simply never calls it, leaving the row
    ``pending`` for retry.  Best-effort: failures are logged and swallowed.

    Validates: Requirements 3.10
    """
    try:
        db = get_client()
        db.table(_TABLE).update({
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("topic", topic).execute()
    except Exception as exc:
        logger.warning("[backlog] persist_status(%s -> %s) failed: %s", topic, status, exc)


# ---------------------------------------------------------------------------
# Adjacency / engagement growth
# ---------------------------------------------------------------------------

def _propose_candidates(seed_topic: str) -> list[BacklogItem]:
    """Propose related-topic candidates for a seed slug as pending Backlog_Items.

    Reuses ``recommendation_agent._generate_related_topics`` (the existing
    GPT-backed "next topics in the same domain" helper) to turn one seed slug
    into related slugs, then maps each proposal to a pending ``BacklogItem``
    levelled from the proposal's ``difficulty`` (degrading to the default level
    when missing/invalid). Best-effort: any failure yields an empty list so the
    caller never blocks or crashes. Returned candidates are NOT yet deduped or
    bounded -- that is the pure core's job in the caller.
    """
    try:
        from app.agents.recommendation_agent import _generate_related_topics

        proposals = _generate_related_topics([seed_topic]) or []
    except Exception as exc:
        logger.warning("[backlog] proposing candidates for '%s' failed: %s", seed_topic, exc)
        return []

    candidates: list[BacklogItem] = []
    seen: set[str] = set()
    for proposal in proposals:
        slug = (proposal.get("slug") or "").strip()
        if not slug or slug == seed_topic or slug in seen:
            continue
        seen.add(slug)
        difficulty = proposal.get("difficulty")
        level = difficulty if difficulty in _VALID_LEVELS else _DEFAULT_LEVEL
        candidates.append(BacklogItem(
            topic=slug,
            level=level,
            priority=_DEFAULT_PRIORITY,
            status="pending",
        ))
    return candidates


def _grow_and_persist(seed_topic: str, label: str) -> list[BacklogItem]:
    """Shared growth path: propose -> dedupe/bound via pure core -> persist.

    Proposes related candidates for ``seed_topic``, routes them through the pure
    :func:`~app.services.topic_frontier.enqueue_adjacent` against the current
    pending backlog so dedupe, the backlog bound, and the per-seed adjacency cap
    (:data:`MAX_ADJACENT_PER_SEED`) are all honored, persists only the newly
    accepted items via :func:`upsert_items`, and returns them. Best-effort: any
    failure is logged and an empty list returned.
    """
    candidates = _propose_candidates(seed_topic)
    if not candidates:
        return []

    existing = load_pending()
    existing_topics = {item.topic for item in existing}

    # The pure core enforces dedupe, the MAX_BACKLOG bound, and the per-seed cap.
    grown = enqueue_adjacent(existing, candidates, cap=MAX_ADJACENT_PER_SEED)

    # Newly accepted items are those in the grown list absent from the original.
    new_items = [item for item in grown if item.topic not in existing_topics]
    if new_items:
        upsert_items(new_items)
        logger.info(
            "[backlog] %s for '%s' spawned %d topic(s): %s",
            label, seed_topic, len(new_items), [item.topic for item in new_items],
        )
    return new_items


def spawn_adjacent_for(topic: str) -> list[BacklogItem]:
    """Spawn up to ``MAX_ADJACENT_PER_SEED`` adjacent topics for a seeded topic.

    Called after a topic is successfully seeded to grow the Topic_Frontier with
    related slugs (reusing ``recommendation_agent`` / ``topic_expansion`` logic).
    Candidates are levelled, status ``pending``, and routed through the pure
    ``enqueue_adjacent`` for dedupe and bounding before being persisted. Returns
    the newly enqueued Backlog_Items. Best-effort: failures are logged and
    swallowed.

    Validates: Requirements 3.3, 3.7, 3.8, 3.9
    """
    return _grow_and_persist(topic, "spawn_adjacent_for")


def spawn_from_engagement(topic: str) -> list[BacklogItem]:
    """Spawn up to ``MAX_ADJACENT_PER_SEED`` related topics from an engagement signal.

    Called when learner engagement on ``topic`` signals demand for nearby
    subjects. Identical growth path to :func:`spawn_adjacent_for`: propose
    related slugs, cap at :data:`MAX_ADJACENT_PER_SEED`, level them, set status
    ``pending``, and route through the pure ``enqueue_adjacent`` for dedupe and
    bounding before persisting. Returns the newly enqueued Backlog_Items.
    Best-effort: failures are logged and swallowed.

    Validates: Requirements 3.4, 3.7, 3.8, 3.9
    """
    return _grow_and_persist(topic, "spawn_from_engagement")


# ---------------------------------------------------------------------------
# Telemetry -> reseed loop (Req 7)
# ---------------------------------------------------------------------------

def _persist_reseed_items(items: list[BacklogItem]) -> bool:
    """Persist newly enqueued reseed Backlog_Items, reporting success/failure.

    Unlike :func:`upsert_items` (which swallows its result), this returns a bool
    so the reseed loop can honor Req 7.8: when the write fails the Topic_Frontier
    state is preserved (nothing was persisted), the items are left for a later
    retry, and the caller records an error indication. Best-effort: any failure is
    logged and reported as ``False`` rather than raised.
    """
    if not items:
        return True
    try:
        db = get_client()
        rows = [_item_to_row(item) for item in items]
        db.table(_TABLE).upsert(rows, on_conflict="topic").execute()
        return True
    except Exception as exc:
        logger.warning(
            "[backlog] reseed enqueue failed for %d item(s): %s", len(items), exc
        )
        return False


def _clip_signals_from_rows(rows: list[dict]) -> list[ClipQualitySignal]:
    """Map ``analytics.worst_clips`` rows to per-Clip Watch_Quality signals.

    Each row's ``avg_watch_ratio`` is the Clip's Watch_Quality and ``views`` is its
    measured view count; the row's ``topic_slug`` is the Clip's parent Topic, which
    the reseed Backlog_Item will target (Req 7.7). Rows with no measurable watch
    ratio (no events / no duration) or no parent Topic are dropped -- there is no
    signal to act on. The Minimum_Views floor is applied by the pure core, not
    here, so a single source of truth governs eligibility.
    """
    signals: list[ClipQualitySignal] = []
    for r in rows:
        ratio = r.get("avg_watch_ratio")
        slug = r.get("topic_slug")
        clip_id = r.get("clip_id")
        if ratio is None or not slug or not clip_id:
            continue
        signals.append(ClipQualitySignal(
            clip_id=clip_id,
            topic_slug=slug,
            watch_quality=float(ratio),
            views=int(r.get("views") or 0),
        ))
    return signals


def _topic_signals_from_rows(rows: list[dict]) -> list[WatchQualitySignal]:
    """Roll ``analytics.worst_clips`` rows up to per-Topic Watch_Quality signals.

    Computes each Topic's view-weighted average Watch_Ratio over its measurable
    Clips -- the same view-weighted rollup ``scripts/diagnose_clips.py`` reports as
    the "worst topics" list -- so the per-Topic identification in Req 7.2 runs over
    exactly that signal. Topics are emitted in first-seen row order so the result
    is deterministic for a deterministic ``worst_clips`` ordering.
    """
    acc: dict[str, dict] = {}
    order: list[str] = []
    for r in rows:
        ratio = r.get("avg_watch_ratio")
        slug = r.get("topic_slug")
        if ratio is None or not slug:
            continue
        views = int(r.get("views") or 0)
        if slug not in acc:
            acc[slug] = {"views": 0, "wsum": 0.0}
            order.append(slug)
        acc[slug]["views"] += views
        acc[slug]["wsum"] += float(ratio) * views

    signals: list[WatchQualitySignal] = []
    for slug in order:
        a = acc[slug]
        views = a["views"]
        quality = (a["wsum"] / views) if views else 0.0
        signals.append(WatchQualitySignal(
            topic_slug=slug, watch_quality=quality, views=views
        ))
    return signals


def reseed_from_telemetry(
    *,
    recut_threshold: float = DEFAULT_RECUT_THRESHOLD,
    minimum_views: int = DEFAULT_MINIMUM_VIEWS,
    limit: int | None = None,
) -> dict:
    """Close the telemetry -> reseed loop: drive the frontier from Watch_Quality.

    This is the thin I/O shell for the Reseed_Prioritizer pure core. It (1) pulls
    the Watch_Quality signal ``analytics.worst_clips`` already computes from
    ``clip_events``, (2) calls the pure core to identify the low-Watch_Quality
    Clips and Topics that clear the Minimum_Views floor and assign each a
    Reseed_Priority, and (3) merges the resulting reseed Backlog_Items into
    ``topic_backlog`` through the **existing** :func:`topic_frontier.enqueue`
    dedupe so a Topic that already has a non-done Backlog_Item is never duplicated
    (Req 7.4, 7.7). A low-Watch_Quality Clip enqueues a Backlog_Item for its parent
    Topic (Req 7.7); the pure core has already mapped Clip -> parent Topic, and the
    shared ``enqueue`` dedupe collapses a Topic identified by several of its Clips
    (and by the Topic rollup) to a single non-done item.

    Newly enqueued items are persisted in one upsert. IF that write fails, the
    Topic_Frontier state is preserved (nothing was persisted), the items are left
    for a later retry, and an error indication is recorded in the returned summary
    (Req 7.8). The subsequent ``Seeding_Worker.run_once`` then drains these reseed
    items through ``ingestion_pipeline.ingest_topic`` unchanged (Req 7.5).

    Best-effort: any read failure degrades to a no-op summary so the loop can
    never block or crash the request path.

    Args:
        recut_threshold: The Recut_Threshold (valid [0.0, 1.0]).
        minimum_views: The Minimum_Views floor (valid [1, 100]).
        limit: Optional cap on the number of ``worst_clips`` rows considered.

    Returns:
        A summary dict ``{identified, enqueued, error}`` where ``identified`` is the
        number of distinct Topics identified for reseeding, ``enqueued`` is the
        number of new non-done Backlog_Items persisted, and ``error`` is ``None`` on
        success or an indication string when the enqueue write failed.

    Validates: Requirements 7.4, 7.7, 7.8
    """
    summary: dict = {"identified": 0, "enqueued": 0, "error": None}

    # Lazy import keeps the analytics DB-touching module off this shell's import
    # graph until the loop actually runs.
    from app.services.analytics import worst_clips

    try:
        rows = worst_clips(min_views=minimum_views, limit=limit)
    except Exception as exc:
        logger.warning("[backlog] reseed: worst_clips read failed: %s", exc)
        summary["error"] = "telemetry_read_failed"
        return summary

    if not rows:
        return summary

    # The pure core identifies both Clips (-> parent Topic) and Topics, and assigns
    # each a Reseed_Priority. Both kinds of result are Topic-targeted Backlog_Items.
    reseed_items = identify_clips_to_reseed(
        _clip_signals_from_rows(rows), recut_threshold, minimum_views
    ) + identify_topics_to_reseed(
        _topic_signals_from_rows(rows), recut_threshold, minimum_views
    )
    if not reseed_items:
        return summary

    # Merge through the existing dedupe: a Topic with a non-done Backlog_Item is
    # left unchanged and never duplicated (Req 7.4, 7.7). load_pending returns only
    # non-done items, which is exactly the dedupe scope the requirement defines.
    existing = load_pending()
    existing_topics = {item.topic for item in existing}
    working = existing
    for item in reseed_items:
        working = enqueue(working, item)

    new_items = [item for item in working if item.topic not in existing_topics]
    summary["identified"] = len({item.topic for item in reseed_items})

    if not new_items:
        # Everything identified already has a non-done Backlog_Item; nothing to do.
        return summary

    if _persist_reseed_items(new_items):
        summary["enqueued"] = len(new_items)
        logger.info(
            "[backlog] reseed enqueued %d new topic(s): %s",
            len(new_items), [item.topic for item in new_items],
        )
    else:
        # Req 7.8: write failed -> frontier preserved, items retained for retry.
        summary["error"] = "enqueue_failed"

    return summary
