import os
import re
import json
import logging
from openai import OpenAI
from app.services.embeddings import embed_texts

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

_openai_client = None
MODEL = "gpt-4o-mini"


def _get_client():
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _openai_client


def process_video(video_url: str, topic_slug: str) -> list[dict]:
    """Transcript pipeline: TranscriptAPI fetches captions → GPT segments → YouTube embed clips."""
    from app.services.youtube import _fetch_transcript

    video_id = _extract_video_id(video_url)
    if not video_id:
        logger.warning(f"Could not extract video_id from {video_url}")
        return []

    logger.info(f"Fetching transcript for video_id={video_id} topic={topic_slug}")
    transcript = _fetch_transcript(video_id)
    if not transcript:
        logger.warning(f"No transcript for {video_id}, skipping")
        return []

    logger.info(f"Got {len(transcript)} transcript entries, segmenting...")
    segments = _identify_segments(transcript, topic_slug)
    logger.info(f"Got {len(segments)} segments")

    texts = [seg.get("transcript") or seg.get("title", "") for seg in segments]
    embeddings = embed_texts(texts)

    clips = []
    for seg, emb in zip(segments, embeddings):
        clip: dict = {
            "topic_slug": topic_slug,
            "title": seg["title"],
            "description": seg["description"],
            "video_url": f"https://www.youtube.com/embed/{video_id}?start={int(seg['start'])}&autoplay=1&rel=0&modestbranding=1",
            "thumbnail_url": f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
            "duration_seconds": int(seg["end"] - seg["start"]),
            "transcript": seg["transcript"],
            "source_url": video_url,
            "source_platform": "youtube",
            "hook_score": seg.get("hook_score", 0.5),
        }
        if emb is not None:
            clip["embedding"] = emb
        clips.append(clip)
    return clips


def _extract_video_id(url: str) -> str | None:
    if "v=" in url:
        vid = url.split("v=")[1].split("&")[0]
    elif "youtu.be/" in url:
        vid = url.split("youtu.be/")[1].split("?")[0]
    else:
        return None
    if not re.match(r'^[A-Za-z0-9_-]{11}$', vid):
        return None
    return vid


def _identify_segments(transcript: list[dict], topic_slug: str) -> list[dict]:
    segments_with_times = [
        {"start": s["start"], "end": s["start"] + s["duration"], "text": s["text"]}
        for s in transcript
    ]

    client = _get_client()
    prompt = f"""You are cutting an educational video about "{topic_slug}" into short reels optimized for viewer retention (TikTok-style).

CRITICAL RULE: Every segment MUST open with a hook — the very first words of the segment should grab attention. Strong hooks are:
- A surprising or counterintuitive claim: "Most people believe X, but actually..."
- A question that creates curiosity: "Why does X happen even when Y?"
- A stakes-setter: "If you get this wrong, the whole thing falls apart"
- A counterexample: "Here's where every textbook gets it wrong"
Avoid segments that open with intros, transitions, or "In this section we will..."

Here is the transcript with timestamps:
{json.dumps(segments_with_times[:300], indent=2)}

Identify ONLY 2-3 segments — the single most hook-worthy moments. Each 45-90 seconds long, each covering one clear idea. Prefer cuts that start mid-thought at a moment of tension or revelation. More can be generated later if users engage; quality over quantity.

For each segment, score its hook quality: 1.0 = irresistible opening, 0.5 = adequate, 0.0 = boring intro.
Write the title as a curiosity-gap phrase (max 8 words) — something that makes the viewer NEED to know more.

Return a JSON array only, no other text:
[
  {{
    "title": "Why Nobody Understands This Correctly",
    "description": "One sentence that makes them want to watch",
    "start": 12.5,
    "end": 72.3,
    "transcript": "the text spoken in this segment",
    "hook_score": 0.85
  }}
]"""

    try:
        response = client.chat.completions.create(
            model=MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        logger.error(f"[pipeline] Groq segmentation API call failed for topic={topic_slug}: {e}")
        return []

    raw = response.choices[0].message.content.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    try:
        segments = json.loads(raw.strip())
    except json.JSONDecodeError as e:
        logger.error(f"[pipeline] Failed to parse segmentation JSON for topic={topic_slug}: {e} | raw={raw[:200]}")
        return []

    for seg in segments:
        seg.setdefault("hook_score", 0.5)
        seg["hook_score"] = max(0.0, min(1.0, float(seg["hook_score"])))
    return segments
