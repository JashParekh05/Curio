"""Preview YouTube candidate search + relevance ranking WITHOUT touching the
database or the transcript API. Use it to eyeball how the "medium" duration
filter and semantic ranking reorder real search results.

Hits only the YouTube Data API (search + videos.list). No Supabase reads/writes,
no transcript fetches, no clips stored. Run from the backend/ dir:

    python -m scripts.preview_retrieval "binary search tree insertion and lookup process explained"
    python -m scripts.preview_retrieval "U-2 incident impact on Cold War politics"

Shows the raw YouTube order, then the relevance-ranked order with cosine score,
caption flag, and duration so you can judge whether the right video floats up.
"""
import os
import sys

import requests
from dotenv import load_dotenv

from app.agents.pipeline_agent import _rank_candidates
from app.services.embeddings import embed_text, embed_texts, cosine_similarity

load_dotenv()


def _fetch_candidates(query: str) -> list[dict]:
    """Replicates pipeline_agent._node_search's API calls (medium duration) but
    skips the Supabase search cache so this preview writes nothing."""
    api_key = os.environ["YOUTUBE_API_KEY"]
    search = requests.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={
            "key": api_key, "q": query, "type": "video",
            "videoDuration": "medium", "videoEmbeddable": "true",
            "safeSearch": "strict", "relevanceLanguage": "en",
            "maxResults": 6, "part": "snippet",
        },
        timeout=10,
    )
    search.raise_for_status()
    items = search.json().get("items", [])
    if not items:
        return []

    ids = [i["id"]["videoId"] for i in items]
    details = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"key": api_key, "id": ",".join(ids), "part": "contentDetails,snippet,statistics"},
        timeout=10,
    )
    import re
    durations, captions, views = {}, {}, {}
    if details.ok:
        for v in details.json().get("items", []):
            cd = v.get("contentDetails", {})
            m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", cd.get("duration", ""))
            if m:
                h, mn, s = (int(x or 0) for x in m.groups())
                durations[v["id"]] = h * 3600 + mn * 60 + s
            captions[v["id"]] = cd.get("caption") == "true"
            try:
                views[v["id"]] = int(v.get("statistics", {}).get("viewCount", 0))
            except (TypeError, ValueError):
                views[v["id"]] = 0

    return [
        {
            "video_id": i["id"]["videoId"],
            "title": i["snippet"]["title"],
            "description": i["snippet"].get("description", "")[:200] or None,
            "duration_seconds": durations.get(i["id"]["videoId"], 0),
            "has_caption": captions.get(i["id"]["videoId"], False),
            "view_count": views.get(i["id"]["videoId"], 0),
        }
        for i in items
    ]


def _views(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def _fmt(v: dict) -> str:
    mins = v["duration_seconds"] // 60
    secs = v["duration_seconds"] % 60
    cap = "cc" if v["has_caption"] else "  "
    return f"[{cap}] {mins:>2}:{secs:02d}  {_views(v.get('view_count', 0)):>6}  {v['title']}"


def main() -> None:
    if len(sys.argv) < 2:
        print('Usage: python -m scripts.preview_retrieval "<search query>"')
        raise SystemExit(1)
    query = sys.argv[1]

    print(f"\n=== query: {query!r} ===\n")
    videos = _fetch_candidates(query)
    if not videos:
        print("No results.")
        return

    print(f"--- RAW YOUTUBE ORDER ({len(videos)} medium-length results) ---")
    for v in videos:
        print("  " + _fmt(v))

    # Per-candidate relevance scores (mirrors _rank_candidates' scoring).
    q_vec = embed_text(query)
    texts = [f"{v.get('title', '')} {v.get('description') or ''}".strip() for v in videos]
    vecs = embed_texts(texts)
    sims = {
        v["video_id"]: (cosine_similarity(q_vec, vec) if (q_vec and vec) else None)
        for v, vec in zip(videos, vecs)
    }

    print("\n--- RELEVANCE-RANKED ORDER (what the pipeline transcribes first) ---")
    for rank, v in enumerate(_rank_candidates(videos, query), 1):
        s = sims.get(v["video_id"])
        score = f"{s:.3f}" if s is not None else "  n/a"
        print(f"  {rank}. sim={score}  " + _fmt(v))


if __name__ == "__main__":
    main()
