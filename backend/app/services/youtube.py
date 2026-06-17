"""YouTube transcript fetcher + the single YouTube quota charge site.

The transcript helpers use TranscriptAPI.com to bypass IP-block issues on cloud
hosts. ``youtube_search`` is the one place that spends YouTube Data API v3 quota:
it is cache-first (a ``youtube_search_cache`` hit costs 0 units and never touches
the Key_Pool), and on a miss it selects an affordable Google Cloud project from
the quota pool, persists the 100-unit search charge BEFORE issuing the real
``youtube/v3/search`` request (charge-before-call ordering, so a recorded
increment always precedes spend), then performs the ``videos.list`` metadata call
(1 unit) and caches the result. It returns ``None`` and spends nothing when no
project can afford the search.

Validates: Requirements 2.5, 6.3, 6.4
"""
import os
import logging
from datetime import datetime

import httpx
import requests

from app.services.quota_pool import (
    METADATA_COST,
    SEARCH_COST,
    failover_select,
    select_project,
)
from app.services.quota_store import (
    charge_and_persist,
    configured_projects,
    load_today,
)

logger = logging.getLogger(__name__)

TRANSCRIPT_API_URL = "https://transcriptapi.com/api/v2/youtube/transcript"

#: YouTube Data API v3 endpoints (the single quota-charged surface).
_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"


def _cache_get(video_id: str) -> list[dict] | None:
    """Return cached transcript segments for video_id, or None if not cached."""
    from app.db.supabase import get_client
    try:
        res = (
            get_client()
            .table("transcript_cache")
            .select("segments")
            .eq("video_id", video_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.warning(f"[transcript] cache read failed for {video_id}: {exc}")
        return None
    if res.data and res.data[0].get("segments"):
        return res.data[0]["segments"]
    return None


def _cache_put(video_id: str, segments: list[dict]) -> None:
    """Store transcript segments for video_id (idempotent upsert)."""
    from app.db.supabase import get_client
    try:
        get_client().table("transcript_cache").upsert(
            {"video_id": video_id, "segments": segments},
            on_conflict="video_id",
        ).execute()
    except Exception as exc:
        logger.warning(f"[transcript] cache write failed for {video_id}: {exc}")


def search_cache_get(query: str) -> list[dict] | None:
    """Return cached YouTube search results (video dicts) for a query, or None."""
    from app.db.supabase import get_client
    try:
        res = (
            get_client()
            .table("youtube_search_cache")
            .select("videos")
            .eq("query", query)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.warning(f"[yt-search] cache read failed for query={query!r}: {exc}")
        return None
    if res.data and res.data[0].get("videos"):
        return res.data[0]["videos"]
    return None


def search_cache_put(query: str, videos: list[dict]) -> None:
    """Store YouTube search results for a query (idempotent upsert)."""
    from app.db.supabase import get_client
    try:
        get_client().table("youtube_search_cache").upsert(
            {"query": query, "videos": videos},
            on_conflict="query",
        ).execute()
    except Exception as exc:
        logger.warning(f"[yt-search] cache write failed for query={query!r}: {exc}")


def _fetch_transcript(video_id: str) -> list[dict] | None:
    """Fetch a YouTube transcript via TranscriptAPI.com, caching by video_id.

    Returns list of {start, duration, text} segments, or None on failure.
    Works from any IP (Render etc.) because the request hits TranscriptAPI's
    network, not YouTube directly. Results are cached in Supabase so repeated
    pipeline runs over the same video never re-pay TranscriptAPI.
    """
    cached = _cache_get(video_id)
    if cached is not None:
        logger.info(f"[transcript] cache hit for {video_id} ({len(cached)} segments)")
        return cached

    transcript_api_key = os.environ.get("TRANSCRIPT_API_KEY", "")
    if not transcript_api_key:
        logger.error("[transcript] TRANSCRIPT_API_KEY not set")
        return None

    try:
        resp = httpx.get(
            TRANSCRIPT_API_URL,
            params={"video_url": video_id},
            headers={"Authorization": f"Bearer {transcript_api_key}"},
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        logger.warning(f"[transcript] Network error for {video_id}: {exc}")
        return None

    if resp.status_code != 200:
        logger.warning(f"[transcript] {video_id} returned {resp.status_code}: {resp.text[:200]}")
        return None

    try:
        data = resp.json()
    except Exception as exc:
        logger.warning(f"[transcript] Bad JSON for {video_id}: {exc}")
        return None

    segments = data.get("transcript") or []
    if not segments:
        logger.warning(f"[transcript] No transcript for {video_id} | payload keys: {list(data.keys())}")
        return None

    out: list[dict] = []
    for seg in segments:
        if not isinstance(seg, dict):
            logger.warning(f"[transcript] Skipping non-dict segment for {video_id}: {type(seg)}")
            continue
        text = (seg.get("text") or "").strip()
        if text:
            out.append({
                "start": float(seg.get("start", 0)),
                "duration": float(seg.get("duration", 0.5)),
                "text": text,
            })

    if out:
        _cache_put(video_id, out)
    return out if out else None


def _search_and_describe(query: str, api_key: str, project_id: str,
                         now_utc: datetime | None) -> list[dict] | None:
    """Issue the ``youtube/v3/search`` + ``videos.list`` calls for one project.

    Mirrors the request/response shape of ``pipeline_agent._node_search``. The
    100-unit search charge has ALREADY been persisted by the caller before this
    runs (charge-before-call ordering); here the 1-unit ``videos.list`` metadata
    charge is persisted BEFORE that follow-up call, preserving the same ordering.

    Returns the list of normalized video dicts (possibly empty when the search
    yields no items) on a successful HTTP exchange, or ``None`` when the search
    request itself fails so the caller can fail over to another project (a
    failure often means that project's real quota is exhausted).
    """
    try:
        search = requests.get(
            _SEARCH_URL,
            params={
                "key": api_key,
                "q": query,
                "type": "video",
                # "medium" = 4-20 min: the focused 5-10 min explainer the section
                # planner targets. "short" (<4 min) contradicts that intent.
                "videoDuration": "medium",
                "videoEmbeddable": "true",
                "safeSearch": "strict",
                "relevanceLanguage": "en",
                "maxResults": 6,
                "part": "snippet",
            },
            timeout=10,
        )
    except requests.RequestException as exc:
        logger.warning(
            f"[yt-search] search request errored for project={project_id} "
            f"query={query!r}: {exc}"
        )
        return None

    if not search.ok:
        logger.warning(
            f"[yt-search] search failed for project={project_id} "
            f"query={query!r}: HTTP {search.status_code}"
        )
        return None

    items = search.json().get("items", [])
    if not items:
        # A successful, empty response: trying another project for the same query
        # would return the same nothing, so do not fail over or spend more.
        logger.info(f"[yt-search] no results for query={query!r}")
        return []

    video_ids = [i["id"]["videoId"] for i in items]

    # Charge the 1-unit metadata cost BEFORE the videos.list call (Req 6.3, 6.4).
    # A persistence failure here means we skip the enrichment call rather than
    # spend un-recorded quota; the search results are still usable.
    durations: dict[str, int] = {}
    captions: dict[str, bool] = {}
    views: dict[str, int] = {}
    if charge_and_persist(project_id, METADATA_COST, now_utc=now_utc):
        try:
            details = requests.get(
                _VIDEOS_URL,
                params={
                    "key": api_key,
                    "id": ",".join(video_ids),
                    "part": "contentDetails,snippet,statistics",
                },
                timeout=10,
            )
        except requests.RequestException as exc:
            logger.warning(f"[yt-search] videos.list errored for project={project_id}: {exc}")
            details = None

        if details is not None and details.ok:
            import re
            for v in details.json().get("items", []):
                cd = v.get("contentDetails", {})
                m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", cd.get("duration", ""))
                if m:
                    h, mn, s = (int(x or 0) for x in m.groups())
                    durations[v["id"]] = h * 3600 + mn * 60 + s
                # Uploader-provided caption flag only; auto-captions still fetchable
                # via TranscriptAPI, so this is a soft signal, never a filter.
                captions[v["id"]] = cd.get("caption") == "true"
                try:
                    views[v["id"]] = int(v.get("statistics", {}).get("viewCount", 0))
                except (TypeError, ValueError):
                    views[v["id"]] = 0
    else:
        logger.warning(
            f"[yt-search] skipping videos.list for project={project_id}: "
            f"metadata charge could not be persisted"
        )

    videos: list[dict] = []
    for item in items:
        vid_id = item["id"]["videoId"]
        snippet = item["snippet"]
        videos.append({
            "video_id": vid_id,
            "title": snippet["title"],
            "description": snippet.get("description", "")[:200] or None,
            "thumbnail_url": snippet.get("thumbnails", {}).get("high", {}).get("url"),
            "duration_seconds": durations.get(vid_id, 180),
            "has_caption": captions.get(vid_id, False),
            "view_count": views.get(vid_id, 0),
        })
    return videos


def youtube_search(query: str, *, now_utc: datetime | None = None) -> list[dict] | None:
    """The single YouTube quota charge site.

    Order of operations (Req 2.5, 6.3, 6.4):

    1. Cache-first: a ``youtube_search_cache`` hit costs 0 units and never touches
       the Key_Pool.
    2. On a miss, select an affordable project (``select_project``, then
       ``failover_select`` to skip projects already tried) over today's
       per-project usage from ``load_today``.
    3. Persist the 100-unit search charge with ``charge_and_persist`` BEFORE
       issuing the real ``youtube/v3/search`` request, so a recorded increment
       always precedes spend.
    4. Perform the search + ``videos.list`` (1 unit) and cache the result.

    Returns the list of video dicts on success (empty list when the search yields
    no results), or ``None`` and spends nothing when no project can afford the
    search.

    Validates: Requirements 2.5, 6.3, 6.4
    """
    # 1. Cache-first: zero units, pool untouched.
    cached = search_cache_get(query)
    if cached:
        logger.info(f"[yt-search] cache hit: query={query!r} ({len(cached)} videos, 0 units)")
        return cached

    # 2. Resolve configured project -> api_key, and today's per-project usage.
    key_by_project = dict(configured_projects())
    if not key_by_project:
        logger.error("[yt-search] no YouTube projects configured (YT_PROJECTS/YOUTUBE_API_KEY)")
        return None
    projects = load_today(now_utc)

    tried: set[str] = set()
    while True:
        # Lowest-id affordable project first, then fail over past tried ones.
        project_id = (
            failover_select(projects, SEARCH_COST, frozenset(tried))
            if tried
            else select_project(projects, SEARCH_COST)
        )
        if project_id is None:
            # No project can afford the search: return None and spend nothing.
            if not tried:
                logger.warning(f"[yt-search] no project can afford a search; query={query!r}")
            return None

        tried.add(project_id)
        api_key = key_by_project.get(project_id)
        if not api_key:
            # Pool knows the project but we have no key for it: try another.
            continue

        # 3. Charge BEFORE the HTTP call (charge-before-call ordering). A
        #    fail-closed persistence error makes this project unspendable, so
        #    fail over to the next affordable one.
        if not charge_and_persist(project_id, SEARCH_COST, now_utc=now_utc):
            logger.warning(
                f"[yt-search] search charge could not be persisted for "
                f"project={project_id}; failing over"
            )
            continue

        logger.info(
            f"[yt-search] search: project={project_id} query={query!r} (~100 units)"
        )
        # 4. Perform the search (+ metadata) for this project.
        videos = _search_and_describe(query, api_key, project_id, now_utc)
        if videos is None:
            # Search HTTP failure (often a real per-project quota exhaustion the
            # local count missed): fail over to the next affordable project.
            continue

        if videos:
            search_cache_put(query, videos)
        return videos
