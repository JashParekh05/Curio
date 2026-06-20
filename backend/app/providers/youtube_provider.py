"""The behavior-preserving YouTube Content_Provider.

This is the refactor of the existing YouTube path into one ``ContentProvider``
behind the uniform abstraction (Req 2). It is a THIN adapter over the existing
``app/services/youtube.py`` charge site and the ``pipeline_agent`` embed shape:
it adds no new quota logic of its own and routes ALL search spend through the
single ``youtube.youtube_search`` charge site, so a topic ingested with
``youtube`` as the only enabled provider yields the same persisted clip field
set tagged with ``Provider_Provenance.provider_id == 'youtube'`` (Req 2.5).

Operations (capability-gated, Req 2.1):

  - ``search``: delegates to ``youtube.youtube_search`` -- cache-first via
    ``youtube_search_cache`` (a hit costs 0 units and never touches the
    Key_Pool, Req 2.2, 2.7), charge-before-call against the Key_Pool on a miss
    (100 units, Req 2.4) -- and normalizes each returned video dict into a
    ``SourceItem`` whose ``provider_id`` and ``embed_ref.provider_id`` are both
    ``youtube`` and whose ``embed_ref.external_id`` is the video id, keeping the
    same ``youtube.com/embed/{id}?start=...`` playback shape ``pipeline_agent``
    builds today, bounded to ``max_results`` (Req 1.1).
  - ``fetch_metadata``: reuses the existing ``videos.list`` integration and the
    existing Key_Pool primitives, persisting the 1-unit metadata charge BEFORE
    the call, preserving today's ordering (Req 2.4, 2.6).
  - ``fetch_transcript``: delegates to ``youtube._fetch_transcript`` -- cache-
    first via ``transcript_cache``, TranscriptAPI on a miss -- with no Key_Pool
    charge, since transcripts are not a YouTube-quota operation (Req 2.3, 2.7).

ASCII only.

Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.6, 2.7
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

from app.services.content_provider import (
    EmbedReference,
    ProviderCapability,
    SourceItem,
    bound_results,
)
from app.services.provider_registry import ProviderCostPolicy
from app.services.quota_pool import (
    DAILY_QUOTA,
    METADATA_COST,
    SEARCH_COST,
    select_project,
)

logger = logging.getLogger(__name__)

#: The stable Provider_Id for the YouTube Content_Provider (Req 2.1).
PROVIDER_ID = "youtube"

#: The standard embed query parameters ``pipeline_agent`` applies to every
#: YouTube clip URL today (kept identical so the playback shape is preserved).
_EMBED_PARAMS = {
    "autoplay": "1",
    "rel": "0",
    "modestbranding": "1",
}


def _embed_url(video_id: str, start: int) -> str:
    """Build the YouTube embed URL exactly as ``pipeline_agent`` does today.

    Mirrors ``_node_segment``'s
    ``youtube.com/embed/{id}?start={start}&autoplay=1&rel=0&modestbranding=1``
    shape so the persisted playback descriptor is byte-identical (Req 2.5).
    """
    return (
        f"https://www.youtube.com/embed/{video_id}"
        f"?start={int(start)}&autoplay=1&rel=0&modestbranding=1"
    )


def _playback_params(start: int) -> dict[str, str]:
    """Return the embed playback params capturing the segment ``start``.

    These are the parameters required to render the embed (Req 4.1); the
    canonical ``video_url`` shape is reconstructable from ``external_id`` +
    these params via ``_embed_url``.
    """
    return {"start": str(int(start)), **_EMBED_PARAMS}


def _normalize_video(video: dict) -> SourceItem | None:
    """Normalize one ``youtube_search`` video dict into a ``SourceItem``.

    The video dict shape is the one ``youtube._search_and_describe`` produces:
    ``video_id``, ``title``, ``description``, ``thumbnail_url``,
    ``duration_seconds``, ``has_caption``, ``view_count``. The resulting
    ``SourceItem`` carries ``provider_id == 'youtube'`` and an ``EmbedReference``
    whose ``provider_id`` also equals ``'youtube'`` and whose ``external_id`` is
    the video id (Req 1.5). The transcript is ``None`` at search time -- it is
    fetched lazily through ``fetch_transcript``. Returns ``None`` when the dict
    has no usable video id so the caller can skip it.
    """
    video_id = (video.get("video_id") or "").strip()
    if not video_id:
        return None

    embed_ref = EmbedReference(
        provider_id=PROVIDER_ID,
        external_id=video_id,
        playback_params=_playback_params(0),
    )
    metadata = {
        "title": video.get("title"),
        "description": video.get("description"),
        "thumbnail_url": video.get("thumbnail_url"),
        "has_caption": video.get("has_caption", False),
        "view_count": video.get("view_count", 0),
        "video_url": _embed_url(video_id, 0),
        "source_url": f"https://www.youtube.com/watch?v={video_id}",
        "source_platform": PROVIDER_ID,
    }
    return SourceItem(
        provider_id=PROVIDER_ID,
        embed_ref=embed_ref,
        title=video.get("title") or "",
        duration_seconds=video.get("duration_seconds"),
        transcript=None,
        metadata=metadata,
    )


class YouTubeProvider:
    """The ``youtube`` Content_Provider: a thin adapter over ``youtube.py``.

    Implements the ``ContentProvider`` Protocol. Declares all three
    Provider_Capabilities (Req 2.1) and reuses the existing charge site and
    caches so YouTube behavior is preserved (Req 2.2-2.7).
    """

    #: Provider_Id 'youtube' (Req 2.1).
    provider_id: str = PROVIDER_ID

    #: Declares search, fetch_metadata, and fetch_transcript (Req 2.1).
    capabilities: frozenset[ProviderCapability] = frozenset(
        {
            ProviderCapability.SEARCH,
            ProviderCapability.FETCH_METADATA,
            ProviderCapability.FETCH_TRANSCRIPT,
        }
    )

    @property
    def cost_policy(self) -> ProviderCostPolicy:
        """The YouTube Provider_Cost_Policy: the existing Key_Pool quota (Req 7.5).

        A search costs 100 units and a metadata call 1 unit, reusing the
        ``quota_pool`` ``SEARCH_COST`` / ``METADATA_COST`` constants rather than
        declaring new numbers. The ``spend_budget`` is the per-project daily
        Key_Pool budget (``DAILY_QUOTA``); actual spend enforcement happens in
        the Key_Pool via ``youtube.youtube_search`` -- this policy duplicates no
        counter (Req 2.4, 7.5).
        """
        return ProviderCostPolicy(
            cost_units={
                ProviderCapability.SEARCH: SEARCH_COST,
                ProviderCapability.FETCH_METADATA: METADATA_COST,
            },
            spend_budget=DAILY_QUOTA,
        )

    def search(self, query: str, *, max_results: int) -> list[SourceItem]:
        """Search for candidate Source_Items via the single charge site.

        Delegates to ``youtube.youtube_search``, which is cache-first via
        ``youtube_search_cache`` (a hit costs 0 units and never touches the
        Key_Pool, Req 2.2, 2.7) and, on a miss, selects an affordable project and
        charges the 100-unit search BEFORE the call (Req 2.4). Each returned
        video dict is normalized into a ``SourceItem`` whose ``provider_id`` and
        ``embed_ref.provider_id`` are both ``youtube`` and whose
        ``embed_ref.external_id`` is the video id, keeping today's embed URL
        shape. The list is bounded to ``max_results`` (Req 1.1).
        """
        from app.services.youtube import youtube_search

        videos = youtube_search(query)
        if not videos:
            # None (no affordable project / none configured) or empty results:
            # nothing was spent beyond what youtube_search already accounted.
            return []

        items: list[SourceItem] = []
        for video in videos:
            item = _normalize_video(video)
            if item is not None:
                items.append(item)
        return bound_results(items, max_results)

    def fetch_metadata(self, external_id: str) -> dict:
        """Return normalized metadata for one video via the existing path.

        Reuses the existing ``videos.list`` integration and the existing Key_Pool
        primitives (``select_project`` over ``quota_store.load_today``,
        ``charge_and_persist`` for the 1-unit metadata charge persisted BEFORE
        the call). No new quota counter is introduced; the charge fails closed
        through ``charge_and_persist`` exactly as the existing path does
        (Req 2.4, 2.6). Returns ``{}`` when the id is empty, no project can
        afford the call, the charge cannot be persisted, or the HTTP call fails.
        """
        external_id = (external_id or "").strip()
        if not external_id:
            return {}

        import requests

        from app.services.quota_store import (
            charge_and_persist,
            configured_projects,
            load_today,
        )
        from app.services.youtube import _VIDEOS_URL

        key_by_project = dict(configured_projects())
        if not key_by_project:
            logger.error("[yt-provider] no YouTube projects configured for metadata")
            return {}

        projects = load_today()
        project_id = select_project(projects, METADATA_COST)
        if project_id is None:
            logger.warning(
                "[yt-provider] no project can afford metadata for %s", external_id
            )
            return {}

        api_key = key_by_project.get(project_id)
        if not api_key:
            return {}

        # Charge the 1-unit metadata cost BEFORE the videos.list call (Req 2.4).
        if not charge_and_persist(project_id, METADATA_COST):
            logger.warning(
                "[yt-provider] metadata charge could not be persisted for "
                "project=%s id=%s",
                project_id,
                external_id,
            )
            return {}

        try:
            details = requests.get(
                _VIDEOS_URL,
                params={
                    "key": api_key,
                    "id": external_id,
                    "part": "contentDetails,snippet,statistics",
                },
                timeout=10,
            )
        except requests.RequestException as exc:
            logger.warning(
                "[yt-provider] videos.list errored for id=%s: %s", external_id, exc
            )
            return {}

        if not details.ok:
            logger.warning(
                "[yt-provider] videos.list failed for id=%s: HTTP %s",
                external_id,
                details.status_code,
            )
            return {}

        items = details.json().get("items", [])
        if not items:
            return {}
        return _normalize_metadata(items[0])

    def fetch_transcript(self, external_id: str) -> list[dict] | None:
        """Return the video's time-aligned transcript, or None when unavailable.

        Delegates to ``youtube._fetch_transcript``: cache-first via
        ``transcript_cache``, TranscriptAPI on a miss, result cached (Req 2.3,
        2.7). No Key_Pool charge -- transcripts are not a YouTube-quota
        operation.
        """
        external_id = (external_id or "").strip()
        if not external_id:
            return None

        from app.services.youtube import _fetch_transcript

        return _fetch_transcript(external_id)


def _normalize_metadata(video: dict) -> dict:
    """Normalize one ``videos.list`` item into the metadata dict shape.

    Parses the ISO-8601 ``contentDetails.duration`` into seconds and surfaces the
    same fields the search path exposes (title, description, thumbnail, duration,
    caption flag, view count), so metadata is consistent regardless of how it was
    fetched (Req 2.6).
    """
    snippet = video.get("snippet", {})
    content_details = video.get("contentDetails", {})
    statistics = video.get("statistics", {})

    duration_seconds = 0
    match = re.match(
        r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", content_details.get("duration", "")
    )
    if match:
        hours, minutes, seconds = (int(x or 0) for x in match.groups())
        duration_seconds = hours * 3600 + minutes * 60 + seconds

    try:
        view_count = int(statistics.get("viewCount", 0))
    except (TypeError, ValueError):
        view_count = 0

    thumbnails = snippet.get("thumbnails", {})
    return {
        "video_id": video.get("id"),
        "title": snippet.get("title"),
        "description": (snippet.get("description") or "")[:200] or None,
        "thumbnail_url": thumbnails.get("high", {}).get("url"),
        "duration_seconds": duration_seconds,
        "has_caption": content_details.get("caption") == "true",
        "view_count": view_count,
    }
