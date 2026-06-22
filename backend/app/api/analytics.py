import asyncio
import logging
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_user, is_operator
from app.db.supabase import get_client
from app.services.analytics import topic_dropoff
from app.services.telemetry import build_session_journey, compute_rollup, JourneyEntry, RollupGroup
from app.services.impression_store import (
    fetch_impressions_for_session,
    fetch_impressions_for_user,
    fetch_all_impressions,
    fetch_watch_events,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/dropoff/{topic_slug}")
async def get_topic_dropoff(topic_slug: str, caller_id: str = Depends(require_user)):
    """Per-beat retention funnel for a topic: views, completion rate, average
    watch ratio, and retention relative to the first beat. Read-only."""
    funnel = await asyncio.to_thread(topic_dropoff, topic_slug)
    return {"topic_slug": topic_slug, "funnel": funnel}


def _serialize_journey(entries: list[JourneyEntry]) -> list[dict]:
    """Serialize JourneyEntry dataclasses to JSON-ready dicts.

    `served_at` is a datetime on the entry; render it as a UTC ISO-8601 string so
    the response is plain JSON. Every other field is already JSON-native."""
    serialized: list[dict] = []
    for entry in entries:
        row = asdict(entry)
        served_at = row.get("served_at")
        if served_at is not None:
            row["served_at"] = served_at.isoformat()
        serialized.append(row)
    return serialized


def _session_owner(session_id: str) -> str | None:
    """Resolve the owning user_id of a learning-path session (read-only).

    Mirrors the session-ownership lookup in `feed.py`: returns the path's
    `user_id`, or None when the session is unknown or the lookup fails (the caller
    decides how to treat an unresolved owner)."""
    db = get_client()
    try:
        path = (
            db.table("learning_paths")
            .select("user_id")
            .eq("session_id", session_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.warning(f"[analytics] Failed to fetch user_id for session={session_id}: {e}")
        return None
    return path.data[0].get("user_id") if path.data else None


def _journey_from_impressions(impressions: list) -> list[JourneyEntry]:
    """Read the Watch_Events for a set of Impressions and build the journey.

    Read-only: gathers the relevant session_ids + clip_ids from the Impressions,
    joins the UNCHANGED clip_events via `fetch_watch_events`, and calls the pure
    `build_session_journey`. Performs no writes (Req 5.8)."""
    session_ids = sorted({imp.session_id for imp in impressions if imp.session_id})
    clip_ids = sorted({imp.clip_id for imp in impressions if imp.clip_id})
    watch_events = fetch_watch_events(session_ids, clip_ids)
    return build_session_journey(impressions, watch_events)


@router.get("/journey/session/{session_id}")
async def get_session_journey(session_id: str, caller_id: str = Depends(require_user)):
    """Read-only Session_Journey for one session.

    A non-operator may read only a session they own (the session-ownership check
    reused from `feed.py`); an operator may read any session. Denies with 403 and
    no data otherwise; `require_user` already yields 401 when unauthenticated.
    Performs no writes. (Req 3.1-3.7, 5.1, 5.3, 5.4, 5.6, 5.7, 5.8)"""
    if not is_operator(caller_id):
        owner = await asyncio.to_thread(_session_owner, session_id)
        if owner != caller_id:
            logger.warning(f"[analytics] journey session ownership mismatch: caller={caller_id} session={session_id}")
            raise HTTPException(status_code=403, detail="Access denied")

    impressions = await asyncio.to_thread(fetch_impressions_for_session, session_id)
    entries = await asyncio.to_thread(_journey_from_impressions, impressions)
    return {"session_id": session_id, "journey": _serialize_journey(entries)}


@router.get("/journey/user/{user_id}")
async def get_user_journey(user_id: str, caller_id: str = Depends(require_user)):
    """Read-only Session_Journey spanning all of a user's sessions.

    A cross-user view (`user_id` != caller) requires the Operator role; otherwise
    403 with no data. `require_user` already yields 401 when unauthenticated.
    Performs no writes. (Req 3.8, 5.1, 5.4, 5.7, 5.8)"""
    if user_id != caller_id and not is_operator(caller_id):
        logger.warning(f"[analytics] cross-user journey denied: caller={caller_id} user={user_id}")
        raise HTTPException(status_code=403, detail="Access denied")

    impressions = await asyncio.to_thread(fetch_impressions_for_user, user_id)
    entries = await asyncio.to_thread(_journey_from_impressions, impressions)
    return {"user_id": user_id, "journey": _serialize_journey(entries)}


def _rollup_from_impressions(impressions: list, dimension: str) -> list[RollupGroup]:
    """Read the Watch_Events for a set of Impressions and compute the rollup.

    Read-only: gathers the relevant session_ids + clip_ids from the Impressions,
    joins the UNCHANGED clip_events via `fetch_watch_events`, and calls the pure
    `compute_rollup` (which supports the virtual `seeding_origin` dimension and
    raises ValueError for an unsupported dimension). Performs no writes (Req 5.8)."""
    session_ids = sorted({imp.session_id for imp in impressions if imp.session_id})
    clip_ids = sorted({imp.clip_id for imp in impressions if imp.clip_id})
    watch_events = fetch_watch_events(session_ids, clip_ids)
    return compute_rollup(impressions, watch_events, dimension)


@router.get("/rollup/{dimension}")
async def get_engagement_rollup(
    dimension: str,
    caller_id: str = Depends(require_user),
    user_id: str | None = Query(None),
):
    """Read-only Engagement_Rollup for one Slice_Dimension (incl. the virtual
    `seeding_origin` dimension).

    Authorization (Req 5.2, 5.5, 5.7): a non-operator may request ONLY a rollup
    scoped to themselves — an explicit `user_id` equal to the caller, or no
    `user_id` (which is narrowed to the caller's own Impressions). Any cross-user
    span — a `user_id` other than the caller, or an unscoped/all-users rollup —
    requires the Operator role; otherwise 403 with no data. An operator may scope
    to any `user_id`, or omit it for an all-users aggregate. `require_user` already
    yields 401 when unauthenticated.

    An unsupported dimension makes the pure `compute_rollup` raise ValueError,
    surfaced here as HTTP 400. Performs no writes (Req 4.1-4.11, 5.8)."""
    operator = is_operator(caller_id)
    if not operator:
        # Non-operators are confined to a self-scoped rollup: a cross-user
        # request (or an unscoped all-users request) is denied.
        if user_id is not None and user_id != caller_id:
            logger.warning(f"[analytics] cross-user rollup denied: caller={caller_id} user={user_id}")
            raise HTTPException(status_code=403, detail="Access denied")
        scope_user_id: str | None = caller_id
    else:
        # Operators may scope to any user, or omit user_id for all users.
        scope_user_id = user_id

    if scope_user_id is None:
        impressions = await asyncio.to_thread(fetch_all_impressions)
    else:
        impressions = await asyncio.to_thread(fetch_impressions_for_user, scope_user_id)

    try:
        groups = await asyncio.to_thread(_rollup_from_impressions, impressions, dimension)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "dimension": dimension,
        "user_id": scope_user_id,
        "rollup": [asdict(group) for group in groups],
    }
