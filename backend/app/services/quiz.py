import json
import logging

from app.db.supabase import get_client as get_db
from app.services.llm import get_client, MODEL

logger = logging.getLogger(__name__)

# Fallback cache for when the clip_quizzes table hasn't been migrated yet,
# so quizzes still work (per-process) without the schema change.
_memory_cache: dict[str, dict] = {}

# Clips shorter than this have too little transcript for a fair question.
_MIN_TRANSCRIPT_CHARS = 80


def get_or_generate_quiz(clip_id: str) -> dict | None:
    """Return a cached quiz for the clip, generating one from its transcript if needed.

    Returns None when the clip has no transcript worth quizzing on — the
    frontend treats that as "no quiz, just advance".
    """
    db = get_db()

    try:
        cached = (
            db.table("clip_quizzes")
            .select("question,options,correct_index,explanation")
            .eq("clip_id", clip_id)
            .limit(1)
            .execute()
        )
        if cached.data:
            row = cached.data[0]
            if isinstance(row.get("options"), str):
                row["options"] = json.loads(row["options"])
            return row
    except Exception:
        if clip_id in _memory_cache:
            return _memory_cache[clip_id]

    try:
        clip = (
            db.table("clips")
            .select("title,topic_slug,transcript")
            .eq("id", clip_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.warning(f"[quiz] Failed to fetch clip {clip_id}: {e}")
        return None
    if not clip.data:
        return None

    transcript = clip.data[0].get("transcript")
    if not transcript or len(transcript.strip()) < _MIN_TRANSCRIPT_CHARS:
        return None

    quiz = _generate(clip.data[0]["title"], clip.data[0]["topic_slug"], transcript)
    if quiz is None:
        return None

    try:
        db.table("clip_quizzes").insert({"clip_id": clip_id, **quiz}).execute()
    except Exception:
        _memory_cache[clip_id] = quiz
    return quiz


def _generate(title: str, topic_slug: str, transcript: str) -> dict | None:
    client = get_client()
    topic = topic_slug.replace("-", " ")
    prompt = f"""A learner just watched a short educational clip titled "{title}" about {topic}.

Here is the transcript of the clip:
{transcript[:4000]}

Write ONE multiple-choice question that checks whether they understood the single most important idea in the clip.
Rules:
- The question must be answerable from the clip alone, no outside knowledge.
- Exactly 4 options. Distractors must be plausible misconceptions, not jokes.
- Keep the question and options short — this appears on a phone screen.

Return JSON only, no other text:
{{
  "question": "...",
  "options": ["...", "...", "...", "..."],
  "correct_index": 0,
  "explanation": "one sentence on why the answer is right"
}}"""

    try:
        response = client.chat.completions.create(
            model=MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        logger.warning(f"[quiz] OpenAI call failed for '{title[:60]}': {e}")
        return None

    raw = response.choices[0].message.content.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError as e:
        logger.warning(f"[quiz] Failed to parse JSON for '{title[:60]}': {e}")
        return None

    options = data.get("options")
    correct_index = data.get("correct_index")
    if (
        not isinstance(data.get("question"), str)
        or not isinstance(options, list)
        or len(options) != 4
        or not isinstance(correct_index, int)
        or not 0 <= correct_index < 4
    ):
        logger.warning(f"[quiz] Bad quiz shape for '{title[:60]}': keys={list(data.keys())}")
        return None

    return {
        "question": data["question"],
        "options": [str(o) for o in options],
        "correct_index": correct_index,
        "explanation": data.get("explanation"),
    }
