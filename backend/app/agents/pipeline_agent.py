"""LangGraph agent: YouTube search → transcript → Groq segmentation → Supabase store."""
import logging
from typing import TypedDict, Annotated
import operator

from langgraph.graph import StateGraph, END

logger = logging.getLogger(__name__)


class PipelineState(TypedDict):
    topic_slug: str
    topic_name: str
    search_query: str | None     # section-specific query; overrides default if set
    section_index: int | None    # which section these clips belong to
    section_title: str | None    # this beat's title (for narrative-aware cutting)
    section_description: str | None  # what this beat must teach
    arc_titles: list[str]        # all 4 beat titles in order, for narrative context
    clear_existing: bool         # delete old clips before storing (False for sections 1-3)
    videos: list[dict]           # raw YouTube search items + details
    clips: list[dict]            # segmented clips ready for DB
    stored_count: int
    errors: Annotated[list[str], operator.add]


def _node_search(state: PipelineState) -> dict:
    import os, requests
    from app.services.youtube import search_cache_get, search_cache_put

    query = state.get("search_query") or f"{state['topic_name']} explained"

    # Serve from cache when possible — a YouTube search costs 100 quota units
    # (10k/day free). Caching by query means re-testing the same topics is free.
    cached = search_cache_get(query)
    if cached:
        logger.info(f"[pipeline_agent] search cache hit: query='{query}' ({len(cached)} videos, 0 units)")
        return {"videos": cached}

    api_key = os.environ.get("YOUTUBE_API_KEY", "")
    if not api_key:
        return {"errors": ["YOUTUBE_API_KEY not set"], "videos": []}

    logger.info(f"[pipeline_agent] search: topic={state['topic_slug']} section={state.get('section_index')} query='{query}' (~100 units)")

    search = requests.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={
            "key": api_key,
            "q": query,
            "type": "video",
            # "medium" = 4-20 min. The section planner writes queries targeting a
            # focused 5-10 min explainer; "short" (<4 min) filtered those out and
            # contradicted the query intent, starving segmentation of substance.
            "videoDuration": "medium",
            "videoEmbeddable": "true",
            "safeSearch": "strict",
            "relevanceLanguage": "en",
            "maxResults": 6,
            "part": "snippet",
        },
        timeout=10,
    )
    if not search.ok:
        return {"errors": [f"YouTube search failed: {search.status_code}"], "videos": []}

    items = search.json().get("items", [])
    if not items:
        return {"errors": [f"No results for {state['topic_slug']}"], "videos": []}

    video_ids = [i["id"]["videoId"] for i in items]
    details = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"key": api_key, "id": ",".join(video_ids), "part": "contentDetails,snippet,statistics"},
        timeout=10,
    )
    logger.info(f"[pipeline_agent] videos.list (~1 unit)")

    durations: dict[str, int] = {}
    captions: dict[str, bool] = {}
    views: dict[str, int] = {}
    if details.ok:
        import re
        for v in details.json().get("items", []):
            cd = v.get("contentDetails", {})
            m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", cd.get("duration", ""))
            if m:
                h, mn, s = (int(x or 0) for x in m.groups())
                durations[v["id"]] = h * 3600 + mn * 60 + s
            # Uploader-provided caption flag. NOTE: this is False for videos that
            # only have YouTube auto-generated captions, which TranscriptAPI can
            # still fetch — so we use it as a soft ranking bonus, never a filter.
            captions[v["id"]] = cd.get("caption") == "true"
            try:
                views[v["id"]] = int(v.get("statistics", {}).get("viewCount", 0))
            except (TypeError, ValueError):
                views[v["id"]] = 0

    videos = []
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

    if videos:
        search_cache_put(query, videos)
    return {"videos": videos}


def _popularity_bonus(view_count: int) -> float:
    """Map raw view count to a small [0, 0.1] bonus on a log scale, so a
    well-watched video edges out a similarly-relevant obscure one without
    letting raw popularity override relevance. ~1k views ≈ 0.04, ~1M ≈ 0.075,
    ~100M ≈ 0.10. Capped so a viral video can't dominate the ranking."""
    import math
    if not view_count or view_count <= 0:
        return 0.0
    return min(math.log10(view_count + 1) / 80.0, 0.1)


def _rank_candidates(videos: list[dict], query: str) -> list[dict]:
    """Order search candidates by semantic relevance to the section query so the
    best-matching video is transcribed first (instead of YouTube's raw order).

    Relevance is primary. Caption availability and view count are light
    tiebreakers — a captioned, well-watched video edges out a similarly-relevant
    one, but popularity never overrides a clearly more relevant video (keeps the
    feed from collapsing onto the same few viral hits). Best-effort: if
    embeddings are unavailable or the candidates carry no text, the original
    order is preserved so this can never make selection worse.
    """
    if len(videos) <= 1 or not query:
        return videos
    texts = [f"{v.get('title', '')} {v.get('description') or ''}".strip() for v in videos]
    if not any(texts):
        return videos

    from app.services.embeddings import embed_text, embed_texts, cosine_similarity

    q_vec = embed_text(query)
    if q_vec is None:
        # No embeddings: float captioned, then higher-view videos ahead.
        return sorted(videos, key=lambda v: (0 if v.get("has_caption") else 1,
                                             -_popularity_bonus(v.get("view_count", 0))))

    vecs = embed_texts(texts)

    def _score(pair) -> float:
        v, vec = pair
        sim = cosine_similarity(q_vec, vec) if vec else -1.0
        caption = 0.05 if v.get("has_caption") else 0.0
        return sim + caption + _popularity_bonus(v.get("view_count", 0))

    ranked = sorted(zip(videos, vecs), key=_score, reverse=True)
    order = [v.get("video_id") for v, _ in ranked]
    logger.info(f"[pipeline_agent] ranked {len(videos)} candidates by relevance to query='{query}': {order}")
    return [v for v, _ in ranked]


def _node_transcribe(state: PipelineState) -> dict:
    """Candidate bounding: we search 6 videos for recall, but only transcribe +
    keep the top few that actually have transcripts. Segmentation downstream is
    the bottleneck (one LLM call per video), so capping here cuts time-to-clips
    and OpenAI cost ~6-12x. The first section keeps just one video so the very
    first clip lands fastest (progressive backfill handles the rest).

    Candidates are relevance-ranked first, so the one(s) we keep are the best
    semantic match to the section query, not just YouTube's top result."""
    from app.services.youtube import _fetch_transcript

    query = state.get("search_query") or state.get("topic_name", "")
    candidates = _rank_candidates(state["videos"], query)

    limit = 1 if state.get("section_index") == 0 else 2
    kept, errors = [], []
    for v in candidates:
        if len(kept) >= limit:
            break
        transcript = _fetch_transcript(v["video_id"])
        if transcript:
            v["transcript"] = transcript
            kept.append(v)
        else:
            errors.append(f"No transcript: {v['video_id']}")
    return {"videos": kept, "errors": errors}


def _node_segment(state: PipelineState) -> dict:
    from app.services.pipeline import _identify_segments
    from app.services.embeddings import embed_texts
    topic_slug = state["topic_slug"]
    clips = []

    # When this run belongs to a section, give segmentation the beat's role and
    # the surrounding arc so it cuts a connected mini-story instead of isolated
    # hooks. Non-section runs (e.g. discover seeding) pass None and keep the
    # original standalone behavior.
    section_context = None
    if state.get("section_index") is not None:
        section_context = {
            "section_index": state.get("section_index"),
            "title": state.get("section_title") or "",
            "description": state.get("section_description") or "",
            "arc_titles": state.get("arc_titles") or [],
        }

    for v in state["videos"]:
        vid_id = v["video_id"]
        base = {
            "topic_slug": topic_slug,
            "section_index": state.get("section_index"),
            "title": v["title"],
            "description": v["description"],
            "video_url": f"https://www.youtube.com/embed/{vid_id}?autoplay=1&rel=0&modestbranding=1",
            "thumbnail_url": v["thumbnail_url"],
            "duration_seconds": v["duration_seconds"],
            "transcript": None,
            "source_url": f"https://www.youtube.com/watch?v={vid_id}",
            "source_platform": "youtube",
            "hook_score": 0.5,
        }
        if v.get("transcript"):
            try:
                segments = _identify_segments(v["transcript"], topic_slug, section_context)
            except Exception as exc:
                logger.warning(f"[pipeline_agent] segment failed {vid_id}: {exc}")
                segments = []
            if segments:
                for seg in segments:
                    clips.append({
                        **base,
                        "title": seg["title"],
                        "description": seg.get("description", base["description"]),
                        "video_url": f"https://www.youtube.com/embed/{vid_id}?start={int(seg['start'])}&autoplay=1&rel=0&modestbranding=1",
                        "duration_seconds": int(seg["end"] - seg["start"]),
                        "transcript": seg.get("transcript"),
                        "hook_score": seg.get("hook_score", 0.5),
                    })
            else:
                # Graceful fallback: a transient empty segmentation (LLM error /
                # unparseable JSON) must still yield the base clip, not zero.
                logger.warning(f"[pipeline_agent] segmentation empty for {vid_id}; using base clip")
                clips.append(base)
        else:
            clips.append(base)

    texts = [c.get("transcript") or c.get("title", "") for c in clips]
    embeddings = embed_texts(texts)
    for clip, emb in zip(clips, embeddings):
        if emb is not None:
            clip["embedding"] = emb

    logger.info(f"[pipeline_agent] {len(clips)} clips after segmentation for {topic_slug}")
    return {"clips": clips}


def _node_store(state: PipelineState) -> dict:
    from app.db.supabase import get_client
    db = get_client()
    if state.get("clear_existing", True):
        db.table("clips").delete().eq("topic_slug", state["topic_slug"]).execute()
    stored = 0
    for clip in state["clips"]:
        try:
            db.table("clips").insert(clip).execute()
            stored += 1
        except Exception as exc:
            logger.warning(f"[pipeline_agent] insert failed: {exc}")
    logger.info(f"[pipeline_agent] stored {stored}/{len(state['clips'])} clips for {state['topic_slug']}")
    return {"stored_count": stored}


def build_pipeline_graph() -> StateGraph:
    g = StateGraph(PipelineState)
    g.add_node("search", _node_search)
    g.add_node("transcribe", _node_transcribe)
    g.add_node("segment", _node_segment)
    g.add_node("store", _node_store)
    g.set_entry_point("search")
    g.add_edge("search", "transcribe")
    g.add_edge("transcribe", "segment")
    g.add_edge("segment", "store")
    g.add_edge("store", END)
    return g.compile()


_pipeline_graph = None


def run_pipeline(
    topic_slug: str,
    topic_name: str,
    search_query: str | None = None,
    section_index: int | None = None,
    clear_existing: bool = True,
    section_title: str | None = None,
    section_description: str | None = None,
    arc_titles: list[str] | None = None,
) -> int:
    """Run the full pipeline for a topic (or one section of a topic). Returns clips stored.

    section_title/section_description/arc_titles give the segmenter narrative
    context so a section's clips form a connected mini-story; they're optional
    so non-section callers (discover seeding, recommendations) are unaffected.
    """
    global _pipeline_graph
    if _pipeline_graph is None:
        _pipeline_graph = build_pipeline_graph()

    result = _pipeline_graph.invoke({
        "topic_slug": topic_slug,
        "topic_name": topic_name,
        "search_query": search_query,
        "section_index": section_index,
        "section_title": section_title,
        "section_description": section_description,
        "arc_titles": arc_titles or [],
        "clear_existing": clear_existing,
        "videos": [],
        "clips": [],
        "stored_count": 0,
        "errors": [],
    })
    return result["stored_count"]
