"""Quality re-ranking for YouTube search candidates.

YouTube's relevance order used to be the only signal in the pipeline — the
first search result with a transcript won, regardless of quality. This module
re-ranks the candidate pool locally (no extra API quota) before transcription:

- semantic fit between the video's title/description and the topic (local
  sentence-transformers embeddings, neutral fallback when unavailable)
- engagement priors (view count, like rate) from the statistics part
- duration fitness for cutting 45-90s clips
- clickbait / Shorts-spam penalties

Downstream, _node_transcribe keeps the first 1-2 videos that have transcripts,
so ranking here directly decides which videos become clips.
"""
import logging
import math

logger = logging.getLogger(__name__)

# Candidates scoring below this semantic similarity to the topic are clearly
# off-topic (reaction videos, tangents) and dropped — unless that would leave
# too few candidates to survive transcript availability filtering.
_MIN_RELEVANCE = 0.2
_MIN_SURVIVORS = 3

_CLICKBAIT_MARKERS = (
    "#shorts", "#short", "#fyp", "#viral",
    "you won't believe", "gone wrong", "wait for it",
)


def _semantic_scores(videos: list[dict], topic_text: str) -> list[float] | None:
    """Cosine similarity of each video's title+description to the topic.

    Returns None when the embedding model is unavailable so callers can
    distinguish "neutral" from "measured low".
    """
    from app.services.embeddings import embed_text, embed_texts, cosine_similarity

    topic_vec = embed_text(topic_text)
    if topic_vec is None:
        return None
    texts = [f"{v.get('title', '')}. {v.get('description') or ''}" for v in videos]
    vecs = embed_texts(texts)
    return [max(0.0, cosine_similarity(topic_vec, vec)) if vec else 0.5 for vec in vecs]


def _engagement_score(video: dict) -> float:
    """Log-scaled view count blended with like rate. Neutral 0.5 when stats are
    missing (e.g. cache entries written before stats were collected)."""
    views = video.get("view_count")
    if not views:
        return 0.5
    score = min(1.0, math.log10(views + 1) / 7)  # 10M views ≈ 1.0
    likes = video.get("like_count")
    if likes:
        like_rate = likes / max(views, 1)
        score = 0.8 * score + 0.2 * min(1.0, like_rate / 0.04)  # ≥4% like rate is excellent
    return score


def _duration_fitness(seconds: int) -> float:
    """We cut 45-90s clips: sub-minute videos have nothing to segment, and
    hour-long lectures dilute the transcript the segmentation LLM sees."""
    if seconds < 60:
        return 0.3
    if seconds <= 1200:
        return 1.0
    if seconds <= 2400:
        return 0.6
    return 0.3


def _clickbait_penalty(video: dict) -> float:
    text = f"{video.get('title', '')} {video.get('description') or ''}".lower()
    penalty = 0.0
    if any(m in text for m in _CLICKBAIT_MARKERS):
        penalty += 0.3
    title = video.get("title", "")
    letters = [c for c in title if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.6:
        penalty += 0.15
    return min(penalty, 0.4)


def rank_videos(videos: list[dict], topic_name: str, query: str | None = None) -> list[dict]:
    """Return videos sorted best-first by composite quality score.

    Safe on cached entries that predate stats collection — missing fields
    score neutral rather than sinking the video.
    """
    if len(videos) <= 1:
        return videos

    topic_text = f"{topic_name}. {query}" if query and query != topic_name else topic_name
    sem = _semantic_scores(videos, topic_text)

    scored: list[tuple[float, float, dict]] = []
    for i, v in enumerate(videos):
        relevance = sem[i] if sem is not None else 0.5
        score = (
            0.5 * relevance
            + 0.3 * _engagement_score(v)
            + 0.2 * _duration_fitness(v.get("duration_seconds") or 180)
            - _clickbait_penalty(v)
        )
        scored.append((score, relevance, v))

    # Drop measured-off-topic candidates, but never starve the transcript filter
    if sem is not None:
        on_topic = [s for s in scored if s[1] >= _MIN_RELEVANCE]
        if len(on_topic) >= _MIN_SURVIVORS:
            if len(on_topic) < len(scored):
                logger.info(f"[ranking] dropped {len(scored) - len(on_topic)} off-topic candidates for '{topic_name}'")
            scored = on_topic

    scored.sort(key=lambda t: t[0], reverse=True)
    for score, _, v in scored:
        v["retrieval_score"] = round(score, 4)
    return [v for _, _, v in scored]
