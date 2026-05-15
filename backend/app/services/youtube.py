import os
import logging
import requests

logger = logging.getLogger(__name__)

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"


def search_clips(topic_slug: str, topic_name: str, max_results: int = 6) -> list[dict]:
    """Search YouTube for educational short clips on a topic, return clip dicts ready for DB insert."""
    if not YOUTUBE_API_KEY:
        logger.error("YOUTUBE_API_KEY not set")
        return []

    query = f"{topic_name} explained"
    # Each search costs 100 units. Daily quota: 10,000 units (100 searches/day).
    logger.info(f"[YouTube API] search for topic='{topic_slug}' query='{query}' (~100 units)")
    search_resp = requests.get(SEARCH_URL, params={
        "key": YOUTUBE_API_KEY,
        "q": query,
        "type": "video",
        "videoDuration": "short",  # < 4 minutes
        "videoEmbeddable": "true",
        "safeSearch": "strict",
        "relevanceLanguage": "en",
        "maxResults": max_results,
        "part": "snippet",
    }, timeout=10)

    if not search_resp.ok:
        logger.error(f"YouTube search failed: {search_resp.status_code} {search_resp.text}")
        return []

    items = search_resp.json().get("items", [])
    if not items:
        logger.warning(f"No YouTube results for topic={topic_slug}")
        return []

    video_ids = [item["id"]["videoId"] for item in items]

    # videos.list costs 1 unit per call
    logger.info(f"[YouTube API] videos.list for {len(video_ids)} ids (~1 unit)")
    details_resp = requests.get(VIDEOS_URL, params={
        "key": YOUTUBE_API_KEY,
        "id": ",".join(video_ids),
        "part": "contentDetails,snippet",
    }, timeout=10)

    durations: dict[str, int] = {}
    if details_resp.ok:
        for v in details_resp.json().get("items", []):
            vid_id = v["id"]
            iso = v["contentDetails"]["duration"]  # e.g. PT3M45S
            durations[vid_id] = _parse_iso_duration(iso)

    clips = []
    for item in items:
        vid_id = item["id"]["videoId"]
        snippet = item["snippet"]
        duration = durations.get(vid_id, 180)
        base_clip = {
            "topic_slug": topic_slug,
            "title": snippet["title"],
            "description": snippet.get("description", "")[:200] or None,
            "video_url": f"https://www.youtube.com/embed/{vid_id}?autoplay=1&rel=0&modestbranding=1",
            "thumbnail_url": snippet.get("thumbnails", {}).get("high", {}).get("url"),
            "duration_seconds": duration,
            "transcript": None,
            "source_url": f"https://www.youtube.com/watch?v={vid_id}",
            "source_platform": "youtube",
            "hook_score": 0.5,
        }
        transcript = _fetch_transcript(vid_id)
        if transcript:
            clips.extend(_segment_video(vid_id, topic_slug, base_clip, transcript))
        else:
            clips.append(base_clip)

    return clips


def _fetch_transcript(video_id: str) -> list[dict] | None:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        entries = YouTubeTranscriptApi.get_transcript(video_id, languages=["en", "en-US"])
        return [{"start": e["start"], "duration": e["duration"], "text": e["text"]} for e in entries]
    except Exception as exc:
        logger.warning(f"[transcript] Failed for {video_id}: {exc}")
        return None


def _segment_video(video_id: str, topic_slug: str, base_clip: dict, transcript: list[dict]) -> list[dict]:
    from app.services.pipeline import _identify_segments
    try:
        segments = _identify_segments(transcript, topic_slug)
    except Exception as exc:
        logger.warning(f"[segment] Failed for {video_id}: {exc}")
        return [base_clip]
    if not segments:
        return [base_clip]
    return [{
        **base_clip,
        "title": seg["title"],
        "description": seg.get("description", base_clip["description"]),
        "video_url": f"https://www.youtube.com/embed/{video_id}?start={int(seg['start'])}&end={int(seg['end'])}&autoplay=1&rel=0&modestbranding=1",
        "duration_seconds": int(seg["end"] - seg["start"]),
        "transcript": seg.get("transcript"),
        "hook_score": seg.get("hook_score", 0.5),
    } for seg in segments]


def _parse_iso_duration(iso: str) -> int:
    """Parse ISO 8601 duration (PT#M#S) to seconds."""
    import re
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
    if not m:
        return 180
    h, mn, s = (int(x or 0) for x in m.groups())
    return h * 3600 + mn * 60 + s
