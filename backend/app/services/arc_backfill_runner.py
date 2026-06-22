"""Arc_Backfill + prune/restore: thin, best-effort I/O shell over the pure core.

This module is the I/O shell paired with the pure decision core in
``arc_backfill.py``. Mirroring the convention of ``arc_unifier_store.py`` /
``coverage_view_store.py`` / ``backlog_store.py`` and the design's "pure decision
core, thin best-effort I/O shell" principle, it holds ONLY the Supabase
reads/writes and the call into the shared Ingestion_Pipeline; every decision
(Arc_Complete-ness, whether to backfill, whether to prune) lives in
``arc_backfill`` and is merely applied here.

What it does:

- **Derive a TopicState** for a Topic from read-only projections: its
  Canonical_Arc (via ``arc_unifier_store.load_canonical_arc``), its per-role
  Admitted_Clip counts, whether it has any structure model, whether it is a
  Watched_Topic, and whether an Arc_Backfill is already in flight.
- **Enqueue a backfill off the request path** (Req 3.1, 3.3, 11.1): when the pure
  ``should_enqueue_backfill`` says so, enqueue exactly one job via
  ``ingestion_store.enqueue_job`` (already deduped); the work itself runs
  ``ingestion_pipeline.ingest_topic`` -- the shared decode -> break-down -> map ->
  judge -> admit path that stores NO raw segmentation (Req 3.2).
- **Finalize a completed backfill** (Req 3.4, 3.9): re-derive the TopicState and,
  only when the pure ``is_arc_complete`` holds, set ``topics.arc_complete``; a
  backfill that leaves a Coverage_Gap does not mark the Topic complete and leaves
  it eligible for a future backfill.
- **Fail closed without touching content** (Req 3.10): on a backfill failure it
  leaves the Topic's existing Clips unchanged, keeps serving the feed from
  existing content (it simply does nothing to the clips), and records the
  ``(slug, reason)`` failure.
- **Reversible prune / restore** (Req 3.6): pruning sets ``topics.archived = true``
  (the Topic's rows are retained) and restore clears it, returning the Topic to
  its pre-prune routable state.

The ``topics.archived`` and ``topics.arc_complete`` columns are added by the
Phase 4 additive migration (``migration_content_revamp.sql``). This shell is
written to use them and degrades gracefully (logs and returns a safe default)
when they are not yet present, exactly like ``coverage_view_store.py`` -- so a
backfill or prune pass can never block or crash the request path.

ASCII only.

Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.6, 3.9, 3.10
"""
from __future__ import annotations

import logging

from app.db.supabase import get_client
from app.services import ingestion_store
from app.services.arc_backfill import (
    TopicState,
    is_arc_complete,
    should_enqueue_backfill,
    should_prune,
)
from app.services.arc_unifier_store import load_canonical_arc

logger = logging.getLogger(__name__)

_TOPICS_TABLE = "topics"
_CLIPS_TABLE = "clips"
_JOBS_TABLE = "ingestion_jobs"
_COMPLETED_STATUS = "completed"


# ---------------------------------------------------------------------------
# Reads (best-effort; every read degrades to a safe default on any failure)
# ---------------------------------------------------------------------------

def _read_clips_per_role(db, topic_slug: str) -> dict[int, int]:
    """Per-role Admitted_Clip counts for a Topic, keyed by role ordinal.

    Reads ``clips.role_ordinal`` for the Topic and counts the clips at each
    ordinal. Clips with no ``role_ordinal`` are role-less and do not fill any
    arc role, so they are excluded. Best-effort: any failure yields an empty map,
    which the core treats as every role unfilled.
    """
    try:
        res = (
            db.table(_CLIPS_TABLE)
            .select("role_ordinal")
            .eq("topic_slug", topic_slug)
            .execute()
        )
    except Exception as exc:
        logger.warning("[arc_backfill] read clips for '%s' failed: %s", topic_slug, exc)
        return {}

    counts: dict[int, int] = {}
    for row in (res.data or []):
        ordinal = row.get("role_ordinal")
        if ordinal is None:
            continue
        key = int(ordinal)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _has_structure_model(db, topic_slug: str) -> bool:
    """True iff the Topic has a Role_Arc_Model or a Legacy_Section_Model row.

    Reads at most one row from ``topic_arc_roles`` then ``topic_sections``.
    Best-effort: a missing table or any failure is treated as that model being
    absent, so a Topic with neither readable model is reported structureless.
    """
    for table, column in (("topic_arc_roles", "role"), ("topic_sections", "section_index")):
        try:
            res = (
                db.table(table)
                .select(column)
                .eq("topic_slug", topic_slug)
                .limit(1)
                .execute()
            )
            if res.data:
                return True
        except Exception as exc:
            logger.warning(
                "[arc_backfill] read %s for '%s' failed: %s", table, topic_slug, exc
            )
    return False


def _topic_clip_ids(db, topic_slug: str) -> list[str]:
    """The Clip ids belonging to a Topic. Best-effort: empty list on failure."""
    try:
        res = (
            db.table(_CLIPS_TABLE)
            .select("id")
            .eq("topic_slug", topic_slug)
            .execute()
        )
        return [row["id"] for row in (res.data or []) if row.get("id") is not None]
    except Exception as exc:
        logger.warning("[arc_backfill] read clip ids for '%s' failed: %s", topic_slug, exc)
        return []


def _topic_is_watched(db, topic_slug: str) -> bool:
    """True iff the Topic has any Impression or any Watch_Event on its Clips.

    A Watched_Topic has at least one Impression or one Watch_Event recorded
    against any of its Clips. Reads the Topic's Clip ids, then checks
    ``clip_events`` and ``impressions`` for any matching row. Best-effort: a
    missing table or any failure is treated as no signal, so an unreadable Topic
    is reported unwatched.
    """
    clip_ids = _topic_clip_ids(db, topic_slug)
    if not clip_ids:
        return False
    for table in ("clip_events", "impressions"):
        try:
            res = (
                db.table(table)
                .select("clip_id")
                .in_("clip_id", clip_ids)
                .limit(1)
                .execute()
            )
            if res.data:
                return True
        except Exception as exc:
            logger.warning(
                "[arc_backfill] read %s for '%s' failed: %s", table, topic_slug, exc
            )
    return False


def _backfill_in_flight(db, topic_slug: str) -> bool:
    """True iff a non-completed Ingestion_Job exists for the Topic.

    An Arc_Backfill is "in flight" when the Topic has an ``ingestion_jobs`` row
    whose status is not ``completed`` (queued / running / failed). Best-effort:
    any failure is treated as no job in flight.
    """
    try:
        res = (
            db.table(_JOBS_TABLE)
            .select("status")
            .eq("topic_slug", topic_slug)
            .neq("status", _COMPLETED_STATUS)
            .limit(1)
            .execute()
        )
        return bool(res.data)
    except Exception as exc:
        logger.warning(
            "[arc_backfill] read ingestion_jobs for '%s' failed: %s", topic_slug, exc
        )
        return False


def build_topic_state(topic_slug: str, db=None) -> TopicState:
    """Assemble the read-only :class:`TopicState` the pure core reasons over.

    Reads the Topic's Canonical_Arc, per-role Admitted_Clip counts, structure-
    model presence, watch signal, and in-flight backfill flag. Strictly read-only
    and best-effort throughout: any DB error degrades to a safe default rather
    than raising, so the serving path is never blocked.

    Args:
        topic_slug: The Topic to project.
        db: Optional Supabase client (injected in tests); defaults to the shared
            client.

    Returns:
        The assembled :class:`TopicState`.
    """
    if db is None:
        db = get_client()
    return TopicState(
        topic_slug=topic_slug,
        arc=load_canonical_arc(topic_slug, db=db),
        clips_per_role=_read_clips_per_role(db, topic_slug),
        has_legacy_or_role_model=_has_structure_model(db, topic_slug),
        has_impression_or_watch=_topic_is_watched(db, topic_slug),
        backfill_in_flight=_backfill_in_flight(db, topic_slug),
    )


# ---------------------------------------------------------------------------
# Writes (best-effort; degrade gracefully when columns/tables are absent)
# ---------------------------------------------------------------------------

def _set_arc_complete(db, topic_slug: str, value: bool) -> None:
    """Persist a Topic's ``arc_complete`` flag. Best-effort; never raises.

    The ``topics.arc_complete`` column is added by the Phase 4 additive
    migration; until it exists the update fails and is logged and swallowed.
    """
    try:
        db.table(_TOPICS_TABLE).update({"arc_complete": value}).eq(
            "slug", topic_slug
        ).execute()
        logger.info(
            "[arc_backfill] topic '%s' arc_complete set to %s", topic_slug, value
        )
    except Exception as exc:
        logger.warning(
            "[arc_backfill] set arc_complete for '%s' failed: %s", topic_slug, exc
        )


def _set_archived(db, topic_slug: str, value: bool) -> bool:
    """Persist a Topic's ``archived`` flag. Best-effort; returns success.

    The ``topics.archived`` column is added by the Phase 4 additive migration;
    until it exists the update fails and is logged and swallowed, and False is
    returned so the caller knows the reversible operation did not take effect.
    """
    try:
        db.table(_TOPICS_TABLE).update({"archived": value}).eq(
            "slug", topic_slug
        ).execute()
        return True
    except Exception as exc:
        logger.warning(
            "[arc_backfill] set archived=%s for '%s' failed: %s", value, topic_slug, exc
        )
        return False


# ---------------------------------------------------------------------------
# Backfill enqueue (off the request path)
# ---------------------------------------------------------------------------

def maybe_enqueue_backfill(topic_slug: str, db=None) -> bool:
    """Enqueue exactly one Arc_Backfill for a routed-to Topic when needed.

    Derives the Topic's :class:`TopicState` and routes the decision through the
    pure :func:`~app.services.arc_backfill.should_enqueue_backfill`. When it
    holds -- the Topic is not Arc_Complete and no backfill is in flight -- exactly
    one job is enqueued via :func:`ingestion_store.enqueue_job`, which is itself
    deduped so a duplicate is never created (Req 3.1). When a backfill is already
    in flight, or the Topic is already Arc_Complete, nothing is enqueued and the
    feed continues to serve from existing Clips (Req 3.3). Best-effort: never
    blocks the request path and never raises.

    Args:
        topic_slug: The routed-to Topic.
        db: Optional Supabase client (injected in tests).

    Returns:
        True iff a backfill job was enqueued.

    Validates: Requirements 3.1, 3.3
    """
    if db is None:
        db = get_client()
    try:
        state = build_topic_state(topic_slug, db)
        if not should_enqueue_backfill(state):
            return False
        job = ingestion_store.enqueue_job(topic_slug)
        return job is not None
    except Exception as exc:
        logger.warning(
            "[arc_backfill] maybe_enqueue_backfill('%s') failed: %s", topic_slug, exc
        )
        return False


# ---------------------------------------------------------------------------
# Backfill execution + finalization
# ---------------------------------------------------------------------------

def run_backfill(topic_slug: str, topic_name: str, db=None) -> dict:
    """Run an Arc_Backfill through the shared Ingestion_Pipeline and finalize.

    Routes the Topic through ``ingestion_pipeline.ingest_topic`` -- the shared
    decode -> break-down -> map -> judge -> admit path that stores no raw
    segmentation (Req 3.2). On completion it re-derives the Topic's
    :class:`TopicState` and, only when the pure
    :func:`~app.services.arc_backfill.is_arc_complete` holds, marks
    ``topics.arc_complete``; a backfill that leaves any Coverage_Gap does not mark
    the Topic complete and leaves it eligible for a future backfill (Req 3.4, 3.9).

    On failure it leaves the Topic's existing Clips unchanged, keeps serving the
    feed from existing content (it performs no clip writes on this path), and
    records the ``(slug, reason)`` failure (Req 3.10).

    Best-effort: ``ingest_topic`` itself never raises, but this shell still guards
    the call so an unexpected error cannot propagate into the caller.

    Args:
        topic_slug: The Topic to backfill.
        topic_name: The human-readable Topic name passed to the pipeline.
        db: Optional Supabase client (injected in tests).

    Returns:
        A small result dict: ``{"status": "complete"|"incomplete"|"failed",
        "arc_complete": bool, "reason": str | None}``.

    Validates: Requirements 3.2, 3.4, 3.9, 3.10
    """
    if db is None:
        db = get_client()

    # Lazy import mirrors seeding_worker: keeps the pipeline dependency off the
    # import path of callers that only enqueue or prune.
    from app.services.ingestion_pipeline import ingest_topic

    try:
        summary = ingest_topic(topic_slug, topic_name)
    except Exception as exc:
        reason = str(exc) or exc.__class__.__name__
        _record_backfill_failure(topic_slug, reason)
        return {"status": "failed", "arc_complete": False, "reason": reason}

    # A deferral / hard skip that stored nothing is a backfill failure: existing
    # Clips are untouched and the Topic stays eligible (Req 3.10).
    outcome = getattr(summary, "outcome", None)
    stored = getattr(summary, "stored", 0) or 0
    if outcome in ("deferred", "error") and stored == 0:
        reason = getattr(summary, "deferred_reason", None) or str(outcome)
        _record_backfill_failure(topic_slug, reason)
        return {"status": "failed", "arc_complete": False, "reason": reason}

    # Re-derive state and finalize Arc_Complete only when every role is filled.
    state = build_topic_state(topic_slug, db)
    if is_arc_complete(state):
        _set_arc_complete(db, topic_slug, True)
        return {"status": "complete", "arc_complete": True, "reason": None}

    # Backfill ran but left a Coverage_Gap: not complete, still eligible (Req 3.9).
    return {"status": "incomplete", "arc_complete": False, "reason": None}


def _record_backfill_failure(topic_slug: str, reason: str) -> None:
    """Record an Arc_Backfill failure as ``(slug, reason)`` (Req 3.10).

    The existing Clips are left untouched by every failure path; this only logs
    the failure so an operator can see which Topic failed and why.
    """
    logger.warning(
        "[arc_backfill] backfill failed for topic '%s': %s", topic_slug, reason
    )


# ---------------------------------------------------------------------------
# Reversible prune / restore
# ---------------------------------------------------------------------------

def maybe_prune(topic_slug: str, db=None) -> bool:
    """Prune a Topic iff it is a Structureless_Topic and an Unwatched_Topic.

    Derives the Topic's :class:`TopicState` and routes the decision through the
    pure :func:`~app.services.arc_backfill.should_prune`. When it holds, the Topic
    is pruned via :func:`prune_topic` (a reversible operation -- Req 3.6). A
    watched or structured Topic is never pruned (Req 3.7). Best-effort; never
    raises.

    Args:
        topic_slug: The candidate Topic.
        db: Optional Supabase client (injected in tests).

    Returns:
        True iff the Topic was pruned.

    Validates: Requirements 3.5, 3.6, 3.7
    """
    if db is None:
        db = get_client()
    try:
        state = build_topic_state(topic_slug, db)
        if not should_prune(state):
            return False
        return prune_topic(topic_slug, db=db)
    except Exception as exc:
        logger.warning("[arc_backfill] maybe_prune('%s') failed: %s", topic_slug, exc)
        return False


def prune_topic(topic_slug: str, db=None) -> bool:
    """Mark a Topic as a Pruned_Topic (reversible; rows retained).

    Sets ``topics.archived = true`` so the Topic is excluded from the
    Curriculum_Spine and from feed routing, while every one of its stored rows is
    retained so a later :func:`restore_topic` returns it to its pre-prune routable
    state (Req 3.6). Best-effort; returns whether the write took effect.

    Validates: Requirements 3.6
    """
    if db is None:
        db = get_client()
    ok = _set_archived(db, topic_slug, True)
    if ok:
        logger.info("[arc_backfill] topic '%s' pruned (archived=true)", topic_slug)
    return ok


def restore_topic(topic_slug: str, db=None) -> bool:
    """Restore a Pruned_Topic to its routable state (reverse of the prune).

    Clears ``topics.archived`` so the Topic is once again eligible for the
    Curriculum_Spine and feed routing; because pruning retained every row, this
    returns the Topic to exactly its pre-prune routable state (Req 3.6).
    Best-effort; returns whether the write took effect.

    Validates: Requirements 3.6
    """
    if db is None:
        db = get_client()
    ok = _set_archived(db, topic_slug, False)
    if ok:
        logger.info("[arc_backfill] topic '%s' restored (archived=false)", topic_slug)
    return ok


def is_pruned(topic_slug: str, db=None) -> bool:
    """True iff the Topic is currently a Pruned_Topic (``topics.archived``).

    Best-effort and read-only: until the ``archived`` column exists (Phase 4
    migration) or on any failure, the Topic is reported non-pruned (routable).
    """
    if db is None:
        db = get_client()
    try:
        res = (
            db.table(_TOPICS_TABLE)
            .select("archived")
            .eq("slug", topic_slug)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return False
        return bool(rows[0].get("archived"))
    except Exception as exc:
        logger.warning("[arc_backfill] is_pruned('%s') failed: %s", topic_slug, exc)
        return False
