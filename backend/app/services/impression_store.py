"""Impression_Store — thin, best-effort I/O shell over the `impressions` table.

The correctness-critical logic for telemetry lives in the pure cores of
`telemetry.py`; this module is the tiny I/O wrapper that persists the Impressions
those cores build, mirroring how `topic_dropoff` wraps the pure `compute_dropoff`
and how `record_clip_event` in `app/api/feed.py` best-effort-writes `clip_events`.

`record_impressions` is scheduled via FastAPI `BackgroundTasks` *after* the feed
response has already been sent, so it must **never raise**: a per-row failure is
logged and skipped (the rest of the batch still lands), and a missing
Impression_Store table degrades to a single logged WARNING. This is the same
fail-open posture as `record_clip_event`'s fallback insert (Req 1.1, 1.10, 2.1,
2.2, 2.3, 6.3, 6.4).
"""

import logging

from app.db.supabase import get_client
from app.models.schemas import Impression

logger = logging.getLogger(__name__)


def _is_missing_table_error(error: Exception) -> bool:
    """True when an insert error means the Impression_Store table is absent.

    PostgREST/Supabase surfaces a missing table as PostgreSQL's `undefined_table`
    (SQLSTATE 42P01) or as the PostgREST "could not find the table ... in the
    schema cache" error (code PGRST205). We match on those signals defensively
    against the stringified error since the client wraps them in different
    exception shapes.
    """
    text = str(error).lower()
    return (
        "42p01" in text
        or "pgrst205" in text
        or "does not exist" in text
        or "could not find the table" in text
    )


def _to_row(impression: Impression) -> dict:
    """Serialize an Impression to an insertable row dict.

    Excludes `id` when None so the table's `gen_random_uuid()` default supplies the
    Impression identifier (the final deterministic journey tie-break, Req 3.2).
    """
    row = impression.model_dump()
    if row.get("id") is None:
        row.pop("id", None)
    return row


def record_impressions(impressions: list[Impression]) -> None:
    """Best-effort insert of one row per Impression into the `impressions` table.

    Inserts one row at a time (mirroring `record_clip_event`'s fallback insert) so
    a single bad row cannot lose the rest of the batch. On a per-row failure, logs
    a WARNING naming the affected `clip_id` and continues. If the Impression_Store
    table is absent (PostgREST "relation does not exist"), logs a single WARNING
    that the store is unavailable and returns. Never raises — it runs inside a
    `BackgroundTask` after the feed response is already sent. (Req 1.1, 1.10, 2.1,
    2.2, 2.3, 6.3, 6.4)
    """
    if not impressions:
        return

    try:
        db = get_client()
    except Exception as e:
        logger.warning(f"[impressions] Impression_Store unavailable, skipping {len(impressions)} impression(s): {e}")
        return

    for impression in impressions:
        try:
            db.table("impressions").insert(_to_row(impression)).execute()
        except Exception as e:
            # A missing table means no impression can ever land in this batch —
            # log once and stop rather than emitting one warning per row.
            if _is_missing_table_error(e):
                logger.warning(f"[impressions] Impression_Store unavailable (table missing); skipping impression recording: {e}")
                return
            logger.warning(f"[impressions] Failed to record impression for clip {impression.clip_id}: {e}")


def _is_read_error_recoverable(error: Exception) -> bool:
    """Always-true sentinel kept for symmetry with the write path.

    Reads are best-effort: any error (missing table, transient DB failure,
    malformed response) degrades to an empty result rather than propagating, so
    a journey/rollup renders empty instead of erroring (Req 5.8). Centralized so
    the logged message can distinguish a missing table from other failures.
    """
    return _is_missing_table_error(error)


def fetch_impressions_for_session(session_id: str) -> list[Impression]:
    """Read-only: every Impression recorded for a single session.

    Returns all rows from the `impressions` table whose `session_id` matches,
    mapped onto the `Impression` model. Best-effort: returns `[]` on any DB error
    or when the Impression_Store table is absent, never raising and never writing
    (Req 3.1, 5.8).
    """
    try:
        db = get_client()
        resp = db.table("impressions").select("*").eq("session_id", session_id).execute()
    except Exception as e:
        if _is_missing_table_error(e):
            logger.warning(f"[impressions] Impression_Store unavailable (table missing); returning no impressions for session {session_id}: {e}")
        else:
            logger.warning(f"[impressions] Failed to fetch impressions for session {session_id}: {e}")
        return []
    return _rows_to_impressions(resp.data or [])


def fetch_impressions_for_user(user_id: str) -> list[Impression]:
    """Read-only: every Impression across all of a user's sessions.

    Returns all rows from the `impressions` table whose `user_id` matches, mapped
    onto the `Impression` model. This spans every session the user served clips in
    (the per-user journey, Req 3.8). Best-effort: returns `[]` on any DB error or
    when the table is absent, never raising and never writing (Req 5.8).
    """
    try:
        db = get_client()
        resp = db.table("impressions").select("*").eq("user_id", user_id).execute()
    except Exception as e:
        if _is_missing_table_error(e):
            logger.warning(f"[impressions] Impression_Store unavailable (table missing); returning no impressions for user {user_id}: {e}")
        else:
            logger.warning(f"[impressions] Failed to fetch impressions for user {user_id}: {e}")
        return []
    return _rows_to_impressions(resp.data or [])


def fetch_all_impressions() -> list[Impression]:
    """Read-only: every Impression in the Impression_Store, across all users.

    Backs the Operator's unscoped, all-users Engagement_Rollup (Req 4.1, 5.2):
    when an operator requests a rollup without a `user_id`, the aggregate spans
    every learner. Best-effort: returns `[]` on any DB error or when the table is
    absent, never raising and never writing (Req 5.8). No equivalent self-scoped
    fetch is needed here — non-operators are always narrowed to their own
    `user_id` via `fetch_impressions_for_user`.
    """
    try:
        db = get_client()
        resp = db.table("impressions").select("*").execute()
    except Exception as e:
        if _is_missing_table_error(e):
            logger.warning(f"[impressions] Impression_Store unavailable (table missing); returning no impressions: {e}")
        else:
            logger.warning(f"[impressions] Failed to fetch all impressions: {e}")
        return []
    return _rows_to_impressions(resp.data or [])


def fetch_watch_events(session_ids: list[str], clip_ids: list[str]) -> list[dict]:
    """Read-only: Watch_Events from the UNCHANGED `clip_events` table.

    Reads `clip_id`, `session_id`, `watch_ms`, `completed`, `replay_count`,
    `feedback`, and `created_at` (the latest-wins tiebreak) for the given sessions,
    then attaches each clip's `duration_seconds` (looked up from the `clips` table)
    onto every returned dict so the pure cores `build_session_journey` /
    `compute_rollup` can compute `watch_ratio` from `event['duration_seconds']`.

    `clip_events` is never modified — this is a pure read-join. Best-effort:
    returns `[]` on any DB error or missing table, never raising and never writing
    (Req 5.8).
    """
    if not session_ids:
        return []

    try:
        db = get_client()
        resp = (
            db.table("clip_events")
            .select("clip_id, session_id, watch_ms, completed, replay_count, feedback, created_at")
            .in_("session_id", session_ids)
            .execute()
        )
    except Exception as e:
        if _is_missing_table_error(e):
            logger.warning(f"[impressions] clip_events unavailable (table missing); returning no watch events: {e}")
        else:
            logger.warning(f"[impressions] Failed to fetch watch events for sessions {session_ids}: {e}")
        return []

    events = resp.data or []
    if not events:
        return []

    # Attach each clip's duration_seconds so the pure cores can derive watch_ratio.
    # The caller passes the relevant clip_ids; fall back to the clip_ids actually
    # present on the fetched events so a None duration is still attached for every
    # event even if the caller's list was incomplete.
    wanted_clip_ids = set(clip_ids) | {e.get("clip_id") for e in events if e.get("clip_id")}
    wanted_clip_ids.discard(None)
    durations = _fetch_clip_durations(db, list(wanted_clip_ids))
    for event in events:
        event["duration_seconds"] = durations.get(event.get("clip_id"))
    return events


def _fetch_clip_durations(db, clip_ids: list[str]) -> dict[str, int | None]:
    """Map clip_id -> duration_seconds from the `clips` table (read-only).

    Returns an empty map on any error or missing table so `fetch_watch_events`
    still yields watch events (with `duration_seconds` None), keeping the read
    best-effort (Req 5.8).
    """
    if not clip_ids:
        return {}
    try:
        resp = db.table("clips").select("id, duration_seconds").in_("id", clip_ids).execute()
    except Exception as e:
        logger.warning(f"[impressions] Failed to fetch clip durations for join: {e}")
        return {}
    return {row["id"]: row.get("duration_seconds") for row in (resp.data or []) if row.get("id")}


def _rows_to_impressions(rows: list[dict]) -> list[Impression]:
    """Map raw `impressions` table rows onto the `Impression` model.

    Rows that fail validation are skipped with a WARNING rather than failing the
    whole read, keeping the fetch best-effort (Req 5.8).
    """
    impressions: list[Impression] = []
    for row in rows:
        try:
            impressions.append(Impression(**row))
        except Exception as e:
            logger.warning(f"[impressions] Skipping malformed impression row (clip {row.get('clip_id')}): {e}")
    return impressions
