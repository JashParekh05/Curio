import os
import urllib.parse
import yt_dlp

COOKIES_PATH = os.getenv("YOUTUBE_COOKIES_PATH", "cookies.txt")


def _ydl_opts(max_results: int) -> dict:
    opts = {
        "quiet": True,
        "extract_flat": True,
        "no_warnings": True,
        "skip_download": True,
        "playlistend": max_results,
    }
    if os.path.exists(COOKIES_PATH):
        opts["cookiefile"] = COOKIES_PATH
    return opts


def search_khan_academy(topic_name: str, max_results: int = 4) -> list[dict]:
    """Search within Khan Academy's YouTube channel. Metadata-only — no transcript fetching."""
    encoded = urllib.parse.quote(topic_name)
    url = f"https://www.youtube.com/@khanacademy/search?query={encoded}"
    try:
        with yt_dlp.YoutubeDL(_ydl_opts(max_results)) as ydl:
            result = ydl.extract_info(url, download=False)
        videos = []
        for entry in (result.get("entries") or [])[:max_results]:
            if not entry:
                continue
            video_id = entry.get("id")
            if not video_id:
                continue
            videos.append({
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "title": entry.get("title", ""),
                "description": entry.get("description", ""),
                "platform": "khan_academy",
            })
        return videos
    except Exception:
        query = f"ytsearch{max_results}:{topic_name} Khan Academy"
        return _yt_search(query, max_results)


def _yt_search(query: str, max_results: int) -> list[dict]:
    with yt_dlp.YoutubeDL(_ydl_opts(max_results)) as ydl:
        result = ydl.extract_info(query, download=False)

    videos = []
    for entry in (result.get("entries") or [])[:max_results]:
        if not entry:
            continue
        video_id = entry.get("id")
        if not video_id:
            continue
        videos.append({
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "title": entry.get("title", ""),
            "description": entry.get("description", ""),
            "platform": "khan_academy",
        })
    return videos
