"""Takeaway_Artifact persistence: thin, best-effort I/O shell over the core.

This module is the I/O shell paired with the pure derivation core in
``takeaway.py``. Mirroring the convention of ``coverage_view_store.py`` /
``curriculum_spine_store.py`` and the design's "pure decision core, thin
best-effort I/O shell" principle, it holds ONLY the Supabase reads/writes; the
ordering and summary derivation live in ``takeaway`` and are merely applied here.

What it does:

- **Trigger on the last-clip Impression** (Req 10.1): ``on_impression`` produces
  a Takeaway only when the impressed Clip is the last Admitted_Clip in the
  Topic's Canonical_Arc ordinal order (ascending role ordinal, then descending
  ranking score). An Impression against any earlier Clip is a no-op.
- **Idempotent on ``(learner_id, topic_slug)``** (Req 10.6): when an artifact
  already exists for the pair the stored one is returned and no duplicate is
  produced.
- **Persist so it survives restart and stays retrievable** (Req 10.3, 10.4):
  artifacts are written to the ``takeaway_artifacts`` table and read back by
  ``(learner, topic)`` via ``get_takeaway``.
- **Zero clips -> no artifact** (Req 10.5): when the Topic has no Admitted_Clips
  the trigger produces nothing, leaves any existing artifacts unchanged, and
  records an indication.

The ``takeaway_artifacts`` table is added by the Phase 4 additive migration
(``migration_content_revamp.sql``). This shell is written to use it and degrades
gracefully (logs and returns a safe default) when it is not yet present, exactly
like ``coverage_view_store.py`` -- so a takeaway pass can never block or crash
the request path.

ASCII only.

Validates: Requirements 10.1, 10.3, 10.4, 10.5, 10.6
"""
from __future__ import annotations

import logging

from app.db.supabase import get_client
from app.services.takeaway import Takeaway, TakeawayClip, derive_takeaway

logger = logging.getLogger(__name__)

_TAKEAWAY_TABLE = "takeaway_artifacts"
_CLIPS_TABLE = "clips"


# ---------------------------------------------------------------------------
# Reads (best-effort; every read degrades to a safe default on any failure)
# ---------------------------------------------------------------------------

def _read_admitted_clips(db, topic_slug: str) -> list[TakeawayClip]:
    """Read a Topic's Admitted_Clips as TakeawayClips.

    An Admitted_Clip is a Clip persisted with a role ordinal. Rows are mapped to
    :class:`TakeawayClip`; a missing ``final_score`` is left None for the pure
    core to treat as 0.0. Best-effort: any DB error degrades to an empty list, so
    the Topic is treated as having no content to summarize.
    """
    try:
        res = (
            db.table(_CLIPS_TABLE)
            .select("id, role_ordinal, final_score, title, description")
            .eq("topic_slug", topic_slug)
            .execute()
        )
        rows = res.data or []
    except Exception as exc:
        logger.warning(
            "[takeaway] read clips for topic '%s' failed: %s", topic_slug, exc
        )
        return []

    clips: list[TakeawayClip] = []
    for row in rows:
        clip_id = row.get("id")
        if clip_id is None:
            continue
        ordinal = row.get("role_ordinal")
        clips.append(
            TakeawayClip(
                clip_id=clip_id,
                role_ordinal=int(ordinal) if ordinal is not None else None,
                final_score=row.get("final_score"),
                title=row.get("title") or "",
                description=row.get("description"),
            )
        )
    return clips


def get_takeaway(learner_id: str, topic_slug: str, db=None) -> Takeaway | None:
    """Return the persisted Takeaway_Artifact for a (learner, Topic) pair.

    Reads the ``takeaway_artifacts`` row keyed by ``(learner_id, topic_slug)`` and
    reconstructs the :class:`Takeaway`, so an artifact produced earlier remains
    retrievable after the Topic's feed session ends and across a process restart
    (Req 10.3, 10.4). Best-effort and read-only: any DB error (including the table
    not yet existing) degrades to ``None``.

    Args:
        learner_id: The learner the artifact belongs to.
        topic_slug: The Topic the artifact summarizes.
        db: Optional Supabase client (injected in tests).

    Returns:
        The stored :class:`Takeaway`, or ``None`` when none exists.

    Validates: Requirements 10.3, 10.4
    """
    if db is None:
        db = get_client()
    try:
        res = (
            db.table(_TAKEAWAY_TABLE)
            .select("learner_id, topic_slug, points")
            .eq("learner_id", learner_id)
            .eq("topic_slug", topic_slug)
            .limit(1)
            .execute()
        )
        rows = res.data or []
    except Exception as exc:
        logger.warning(
            "[takeaway] read artifact (%s, %s) failed: %s",
            learner_id, topic_slug, exc,
        )
        return None

    if not rows:
        return None
    row = rows[0]
    points = row.get("points") or []
    return Takeaway(
        learner_id=row.get("learner_id", learner_id),
        topic_slug=row.get("topic_slug", topic_slug),
        points=tuple(points),
    )


# ---------------------------------------------------------------------------
# Trigger helpers
# ---------------------------------------------------------------------------

def _last_arc_clip_id(clips: list[TakeawayClip]) -> str | None:
    """The id of the last Admitted_Clip in Canonical_Arc ordinal order.

    Reuses the pure core's derivation to find the final Clip in the
    ascending-ordinal, descending-score order. Returns ``None`` when there are no
    clips.
    """
    takeaway = derive_takeaway("", "", clips)
    if takeaway is None:
        return None
    # Re-derive the ordered ids to identify the final clip deterministically.
    from app.services.takeaway import _order_key

    ordered = sorted(clips, key=_order_key)
    return ordered[-1].clip_id if ordered else None


# ---------------------------------------------------------------------------
# Production (idempotent, best-effort)
# ---------------------------------------------------------------------------

def produce_takeaway(learner_id: str, topic_slug: str, db=None) -> Takeaway | None:
    """Produce (or return the existing) Takeaway_Artifact for a (learner, Topic).

    Idempotent on ``(learner_id, topic_slug)`` (Req 10.6): when an artifact
    already exists the stored one is returned and no duplicate is produced. When
    none exists the Topic's Admitted_Clips are read and the pure
    :func:`~app.services.takeaway.derive_takeaway` produces the artifact, which is
    then persisted so it survives restart and stays retrievable (Req 10.3, 10.4).

    When the Topic has zero Admitted_Clips no artifact is produced, any existing
    stored artifacts are left unchanged, and an indication is recorded (Req 10.5).
    Best-effort: any DB write error is logged and swallowed.

    Args:
        learner_id: The learner the artifact belongs to.
        topic_slug: The Topic to summarize.
        db: Optional Supabase client (injected in tests).

    Returns:
        The existing or newly produced :class:`Takeaway`, or ``None`` when the
        Topic had no content to summarize.

    Validates: Requirements 10.3, 10.4, 10.5, 10.6
    """
    if db is None:
        db = get_client()

    # Idempotency: an existing artifact is returned without re-producing (Req 10.6).
    existing = get_takeaway(learner_id, topic_slug, db)
    if existing is not None:
        return existing

    clips = _read_admitted_clips(db, topic_slug)
    takeaway = derive_takeaway(learner_id, topic_slug, clips)
    if takeaway is None:
        # Zero clips: produce nothing, leave existing artifacts unchanged (Req 10.5).
        logger.info(
            "[takeaway] topic '%s' had no content to summarize for learner '%s'",
            topic_slug, learner_id,
        )
        return None

    try:
        db.table(_TAKEAWAY_TABLE).upsert(
            {
                "learner_id": takeaway.learner_id,
                "topic_slug": takeaway.topic_slug,
                "points": list(takeaway.points),
            },
            on_conflict="learner_id,topic_slug",
        ).execute()
        logger.info(
            "[takeaway] produced artifact (%s, %s) with %d points",
            learner_id, topic_slug, len(takeaway.points),
        )
    except Exception as exc:
        logger.warning(
            "[takeaway] persist artifact (%s, %s) failed: %s",
            learner_id, topic_slug, exc,
        )
    return takeaway


def on_impression(
    learner_id: str, topic_slug: str, clip_id: str, db=None
) -> Takeaway | None:
    """Trigger Takeaway production on an Impression against the last arc Clip.

    Produces exactly one Takeaway_Artifact when ``clip_id`` is the last
    Admitted_Clip in the Topic's Canonical_Arc ordinal order -- ascending role
    ordinal, then descending ranking score (Req 10.1). An Impression against any
    earlier Clip is a no-op and returns ``None``. When the trigger fires,
    production is delegated to :func:`produce_takeaway`, which is idempotent on
    ``(learner_id, topic_slug)`` so re-triggering returns the existing artifact
    without producing a duplicate (Req 10.6). Best-effort throughout.

    Args:
        learner_id: The learner the Impression is for.
        topic_slug: The Topic the impressed Clip belongs to.
        clip_id: The impressed Clip.
        db: Optional Supabase client (injected in tests).

    Returns:
        The produced or existing :class:`Takeaway` when the trigger fires and the
        Topic has content, else ``None``.

    Validates: Requirements 10.1, 10.5, 10.6
    """
    if db is None:
        db = get_client()

    # If the artifact already exists, re-triggering returns it without producing
    # a duplicate (Req 10.6), regardless of which clip drove the Impression.
    existing = get_takeaway(learner_id, topic_slug, db)
    if existing is not None:
        return existing

    clips = _read_admitted_clips(db, topic_slug)
    last_id = _last_arc_clip_id(clips)
    if last_id is None:
        # Zero Admitted_Clips: nothing to summarize (Req 10.5).
        logger.info(
            "[takeaway] impression on topic '%s' but no admitted clips", topic_slug
        )
        return None
    if clip_id != last_id:
        # Not the last clip in arc order: the Topic is not finished yet.
        return None

    return produce_takeaway(learner_id, topic_slug, db)
