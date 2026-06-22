"""Progress shell: the Mastery_Compute + Soft_Unlock I/O shell (Phase 3, Req 3).

This is the thin, best-effort I/O shell paired with the pure decision cores
``mastery.topic_mastery`` (per-topic mastery from quiz + completion signals) and
``soft_unlock.topic_unlock`` / ``soft_unlock.level_progress`` (advisory, never
blocking, per-topic and per-level status). Following the repository convention
(see ``coverage_view_store.py`` / ``leveled_path_store.py`` / ``placement.py``),
it owns ONLY the Supabase reads/writes and the glue that assembles the cores'
inputs; every progression decision lives in the pure cores and this module
decides nothing.

For a ``subject`` (a learner's ``user_id`` -- or, for an anonymous learner, a
``session_id``) it:

1. resolves the learner's leveled path(s) from ``learning_paths`` (the serialized
   ``LeveledPath`` projection in the nullable ``levels`` column, written by the
   Phase 1 ``leveled_path_store`` shell);
2. reads ``quiz_results`` correct counts per topic via the existing
   :func:`~app.services.quiz.summarize_mastery` shape;
3. reads ``clip_events`` completion counts (completed vs seen clips), attributed
   to topics by joining ``clips.topic_slug``;
4. calls :func:`~app.services.mastery.topic_mastery` per topic and upserts a
   ``learning_progress`` row ``(subject, topic_slug, level, status,
   mastery_score)``; and
5. assembles per-level mastered flags into
   :func:`~app.services.soft_unlock.level_progress` plus a per-topic
   :func:`~app.services.soft_unlock.topic_unlock` status for the progress bar and
   mastery badges.

Best-effort and off the request path (Req 4.3): the ``learning_progress`` table
and the ``learning_paths.levels`` column are introduced by additive operator-run
migrations and may be absent in the DB; every read/write degrades to a safe
default (logs and continues) instead of raising, exactly like the other
best-effort shells. Because every persisted ``mastery_score`` comes straight from
the clamped :func:`~app.services.mastery.topic_mastery` core, the database
``check (mastery_score >= 0 and <= 1)`` constraint can never be violated by this
shell (Req 5.4).

ASCII only.

Validates: Requirements 3.1, 3.3, 4.3, 5.4
"""
from __future__ import annotations

import logging

from app.db.supabase import get_client
from app.services import mastery, soft_unlock
from app.services.quiz import summarize_mastery

logger = logging.getLogger(__name__)

_LEARNING_PATHS_TABLE = "learning_paths"
_LEARNING_PROGRESS_TABLE = "learning_progress"

#: Name of the single implicit level used when a path has no serialized
#: ``LeveledPath`` projection (legacy single-level behavior, Req 5.3).
_IMPLICIT_LEVEL_NAME = "Foundations"


# ---------------------------------------------------------------------------
# Reads (best-effort; degrade to a safe default on any failure)
# ---------------------------------------------------------------------------

def _load_paths(db, subject: str) -> list[dict]:
    """Load the learner's path rows for ``subject`` (best-effort).

    ``subject`` is normally a ``user_id``; the rows owned by that user carry the
    session ids (for the ``clip_events`` join) and the serialized ``LeveledPath``
    projection (for the level grouping). When no rows match on ``user_id`` the
    ``subject`` is retried as a ``session_id`` so an anonymous, session-only
    learner still resolves. Returns ``[]`` on any failure (including the
    ``levels`` column not yet existing -- it is selected separately).
    """
    rows: list[dict] = []
    try:
        res = (
            db.table(_LEARNING_PATHS_TABLE)
            .select("session_id, user_id, topic_slugs")
            .eq("user_id", subject)
            .execute()
        )
        rows = list(res.data or [])
    except Exception as exc:
        logger.warning("[progress] load paths by user_id failed for subject=%s: %s", subject, exc)
        rows = []

    if not rows:
        try:
            res = (
                db.table(_LEARNING_PATHS_TABLE)
                .select("session_id, user_id, topic_slugs")
                .eq("session_id", subject)
                .execute()
            )
            rows = list(res.data or [])
        except Exception as exc:
            logger.warning("[progress] load path by session_id failed for subject=%s: %s", subject, exc)
            rows = []
    return rows


def _load_levels_projection(db, session_ids: list[str]) -> list[dict]:
    """Read the serialized ``LeveledPath`` projection for the learner's sessions.

    Done as a SEPARATE best-effort query (mirroring ``feed.get_path_feed``) so a
    missing ``learning_paths.levels`` column -- the additive Phase 1 migration may
    not be applied yet -- degrades to "no projection" (an empty list) instead of
    breaking the main read. Returns the first non-empty serialized projection
    found, as a list of ``{"ordinal", "name", "topic_slugs"}`` dicts; ``[]`` when
    none is present.
    """
    if not session_ids:
        return []
    try:
        res = (
            db.table(_LEARNING_PATHS_TABLE)
            .select("levels")
            .in_("session_id", session_ids)
            .execute()
        )
    except Exception as exc:
        logger.warning("[progress] read learning_paths.levels failed (column may be absent): %s", exc)
        return []
    for row in (res.data or []):
        payload = row.get("levels")
        if isinstance(payload, list) and payload:
            return payload
    return []


def _quiz_counts(db, subject: str, session_ids: list[str]) -> dict[str, dict]:
    """Per-topic quiz answered/correct/points for the subject (best-effort).

    Reads ``quiz_results`` (``topic_slug, correct, points``) for the learner --
    matched by ``user_id`` and, additionally, by any of the learner's session ids
    -- and aggregates them through the existing
    :func:`~app.services.quiz.summarize_mastery` shape. Returns the
    ``{slug: {answered, correct, points, correct_rate, mastered}}`` map (the
    ``topics`` sub-dict); ``{}`` on any failure.
    """
    rows: list[dict] = []
    try:
        res = (
            db.table("quiz_results")
            .select("topic_slug, correct, points")
            .eq("user_id", subject)
            .execute()
        )
        rows.extend(res.data or [])
    except Exception as exc:
        logger.warning("[progress] read quiz_results by user_id failed for subject=%s: %s", subject, exc)

    if session_ids:
        try:
            res = (
                db.table("quiz_results")
                .select("topic_slug, correct, points")
                .in_("session_id", session_ids)
                .execute()
            )
            rows.extend(res.data or [])
        except Exception as exc:
            logger.warning("[progress] read quiz_results by session failed for subject=%s: %s", subject, exc)

    return summarize_mastery(rows).get("topics", {})


def _completion_counts(db, session_ids: list[str]) -> dict[str, dict]:
    """Per-topic completion counts (distinct clips seen vs completed).

    Reads ``clip_events`` (``clip_id, completed``) for the learner's sessions and
    attributes each event to a topic by joining ``clips.topic_slug``. A clip is
    "seen" when it has any event and "completed" when any of its events is
    ``completed``; both are counted over DISTINCT clip ids so replays never
    inflate the rate. Returns ``{slug: {seen, completed}}``; ``{}`` on any failure
    or when there are no sessions.
    """
    if not session_ids:
        return {}

    try:
        res = (
            db.table("clip_events")
            .select("clip_id, completed")
            .in_("session_id", session_ids)
            .execute()
        )
        events = list(res.data or [])
    except Exception as exc:
        logger.warning("[progress] read clip_events failed: %s", exc)
        return {}

    # Distinct clip -> completed (a clip counts as completed if ANY event did).
    completed_by_clip: dict[str, bool] = {}
    for event in events:
        clip_id = event.get("clip_id")
        if clip_id is None:
            continue
        completed_by_clip[clip_id] = completed_by_clip.get(clip_id, False) or bool(event.get("completed"))

    clip_ids = list(completed_by_clip.keys())
    if not clip_ids:
        return {}

    try:
        res = (
            db.table("clips")
            .select("id, topic_slug")
            .in_("id", clip_ids)
            .execute()
        )
        clip_rows = list(res.data or [])
    except Exception as exc:
        logger.warning("[progress] read clips for completion join failed: %s", exc)
        return {}

    counts: dict[str, dict] = {}
    for row in clip_rows:
        clip_id = row.get("id")
        slug = row.get("topic_slug")
        if clip_id is None or not slug:
            continue
        bucket = counts.setdefault(slug, {"seen": 0, "completed": 0})
        bucket["seen"] += 1
        if completed_by_clip.get(clip_id):
            bucket["completed"] += 1
    return counts


# ---------------------------------------------------------------------------
# Level grouping helpers
# ---------------------------------------------------------------------------

def _level_groups(projection: list[dict], all_slugs: list[str]) -> list[dict]:
    """Build the ordered level grouping from the serialized projection.

    Each level becomes ``{"ordinal", "name", "topic_slugs"}`` in ordinal order.
    When the projection is empty (no Phase 1 migration / legacy path), every
    topic falls into a single implicit ``Foundations`` level (Req 5.3). Any topic
    present in the signals but missing from the projection is appended to the last
    level so no topic is dropped from the response.
    """
    groups: list[dict] = []
    placed: set[str] = set()
    for item in projection:
        if not isinstance(item, dict):
            continue
        slugs = item.get("topic_slugs")
        if not isinstance(slugs, list):
            continue
        level_slugs = [str(s) for s in slugs]
        groups.append(
            {
                "ordinal": int(item.get("ordinal", len(groups) + 1)),
                "name": str(item.get("name") or f"Level {len(groups) + 1}"),
                "topic_slugs": level_slugs,
            }
        )
        placed.update(level_slugs)

    extras = [s for s in all_slugs if s not in placed]
    if not groups:
        return [{"ordinal": 1, "name": _IMPLICIT_LEVEL_NAME, "topic_slugs": list(all_slugs)}]
    if extras:
        groups[-1]["topic_slugs"] = list(groups[-1]["topic_slugs"]) + extras
    return groups


# ---------------------------------------------------------------------------
# Persistence (best-effort upsert)
# ---------------------------------------------------------------------------

def _upsert_progress(db, rows: list[dict]) -> None:
    """Upsert ``learning_progress`` rows best-effort, off the request path.

    Every ``mastery_score`` originates from the clamped
    :func:`~app.services.mastery.topic_mastery` core, so the DB
    ``check (mastery_score >= 0 and <= 1)`` constraint can never be violated here
    (Req 5.4). The table is created by the additive Phase 3 operator-run
    migration and may be absent; a failed upsert (missing table or otherwise) is
    logged and swallowed so progress never blocks or crashes the request path
    (Req 4.3).
    """
    if not rows:
        return
    try:
        db.table(_LEARNING_PROGRESS_TABLE).upsert(
            rows, on_conflict="subject,topic_slug"
        ).execute()
    except Exception as exc:
        logger.warning(
            "[progress] upsert learning_progress failed (table may be absent); "
            "skipping persistence: %s",
            exc,
        )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def compute_progress(subject: str, db=None) -> dict:
    """Compute, persist, and assemble a learner's progress for ``subject``.

    Resolves the learner's leveled path(s), reads the quiz and completion signals,
    runs the pure :func:`~app.services.mastery.topic_mastery` per topic, upserts
    the resulting ``learning_progress`` rows best-effort, and assembles the
    advisory per-level :func:`~app.services.soft_unlock.level_progress` plus a
    per-topic :func:`~app.services.soft_unlock.topic_unlock` status. The
    ``recommended`` status is given to the earliest unmastered topic in
    level-then-path order; the status type has no blocking value, so nothing here
    can ever gate the feed (Req 3.3).

    Best-effort throughout (Req 4.3): any read/write degrades to a safe default
    rather than raising, so the endpoint always returns a well-formed (possibly
    empty) progress object.

    Args:
        subject: The learner's ``user_id`` (or a ``session_id`` for an anonymous
            learner).
        db: Optional Supabase client (injected in tests); defaults to the shared
            client.

    Returns:
        A dict with the per-level ``levels`` (each carrying its
        ``percent_complete`` / ``all_mastered`` and its per-topic statuses), a
        flat ``topics`` map, and ``total_points``.

    Validates: Requirements 3.1, 3.3, 4.3, 5.4
    """
    if db is None:
        db = get_client()

    path_rows = _load_paths(db, subject)
    session_ids = sorted({r.get("session_id") for r in path_rows if r.get("session_id")})

    quiz_topics = _quiz_counts(db, subject, session_ids)
    completion_topics = _completion_counts(db, session_ids)

    # Topics from the path declaration plus anything the signals touched, so the
    # response covers planned-but-untouched topics and touched-but-unplanned ones.
    path_slugs: list[str] = []
    for r in path_rows:
        for slug in (r.get("topic_slugs") or []):
            if slug and slug not in path_slugs:
                path_slugs.append(slug)
    for slug in quiz_topics:
        if slug not in path_slugs:
            path_slugs.append(slug)
    for slug in completion_topics:
        if slug not in path_slugs:
            path_slugs.append(slug)

    projection = _load_levels_projection(db, session_ids)
    groups = _level_groups(projection, path_slugs)

    # Level-then-path serving order (the order the learner advances through).
    ordered_slugs: list[str] = []
    for group in groups:
        for slug in group["topic_slugs"]:
            if slug not in ordered_slugs:
                ordered_slugs.append(slug)

    slug_to_level = {
        slug: group["name"] for group in groups for slug in group["topic_slugs"]
    }

    # Per-topic mastery from the pure core.
    masteries: dict[str, mastery.TopicMastery] = {}
    for slug in ordered_slugs:
        q = quiz_topics.get(slug, {})
        c = completion_topics.get(slug, {})
        masteries[slug] = mastery.topic_mastery(
            topic_slug=slug,
            quiz_correct=int(q.get("correct", 0) or 0),
            quiz_answered=int(q.get("answered", 0) or 0),
            clips_completed=int(c.get("completed", 0) or 0),
            clips_seen=int(c.get("seen", 0) or 0),
        )

    # The earliest unmastered topic in serving order is the single "recommended"
    # next-best focus; everything else is "available" or "mastered".
    next_unmastered: str | None = None
    for slug in ordered_slugs:
        if not masteries[slug].mastered:
            next_unmastered = slug
            break

    # Per-topic advisory status + flat topics map + the rows to persist.
    topics_out: dict[str, dict] = {}
    progress_rows: list[dict] = []
    for slug in ordered_slugs:
        tm = masteries[slug]
        unlock = soft_unlock.topic_unlock(tm.mastered, slug == next_unmastered)
        topics_out[slug] = {
            "mastery_score": tm.mastery_score,
            "status": tm.status,
            "mastered": tm.mastered,
            "unlock": unlock,
            "level": slug_to_level.get(slug),
        }
        progress_rows.append(
            {
                "subject": subject,
                "topic_slug": slug,
                "level": slug_to_level.get(slug),
                "status": tm.status,
                "mastery_score": tm.mastery_score,
            }
        )

    _upsert_progress(db, progress_rows)

    # Per-level advisory progress from the pure core.
    levels_out: list[dict] = []
    for group in groups:
        slugs = group["topic_slugs"]
        flags = [masteries[s].mastered for s in slugs if s in masteries]
        lp = soft_unlock.level_progress(group["ordinal"], flags)
        levels_out.append(
            {
                "ordinal": lp.ordinal,
                "name": group["name"],
                "percent_complete": lp.percent_complete,
                "all_mastered": lp.all_mastered,
                "topics": [
                    {"topic_slug": s, **topics_out[s]} for s in slugs if s in topics_out
                ],
            }
        )

    total_points = sum(int(t.get("points", 0) or 0) for t in quiz_topics.values())

    return {
        "subject": subject,
        "levels": levels_out,
        "topics": topics_out,
        "total_points": total_points,
    }
