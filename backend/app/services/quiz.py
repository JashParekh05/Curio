"""Active-learning quiz: pure helpers for the multiple-choice quiz feature.

This module hosts the deterministic, unit-testable core — question validation,
grading, streak points, and mastery aggregation. The LLM generation/judge stages
and the DB-touching orchestration are added on top (later tasks); they stay thin
so this logic carries the test rigor used across the codebase.

Design ref: .kiro/specs/active-learning-quiz/design.md
"""
import json
import logging

logger = logging.getLogger(__name__)

MODEL = "gpt-4o-mini"
QUESTIONS_PER_TOPIC = 3        # target MCQs to keep per topic
GENERATION_POOL = 6           # candidates to generate (the judge rejects some)
_TRANSCRIPT_BUDGET = 2000      # chars of source excerpt fed to generation

# Scoring + mastery knobs.
POINTS_PER_CORRECT = 10        # base points for a first-time correct answer
STREAK_BONUS_PER = 5           # extra points per consecutive correct beyond the first
MAX_STREAK_BONUS = 40          # clamp so a (client-reported) streak can't inflate points
MASTERED_THRESHOLD = 0.8       # correct-rate at/above which a topic counts as mastered

# A valid MCQ has one correct option and 2-4 total options.
_MIN_OPTIONS = 2
_MAX_OPTIONS = 4


def _validate_question(raw: dict) -> dict | None:
    """Normalize an LLM-produced question to a clean MCQ, or return None.

    Enforces the MCQ invariants (the hard gate): 2-4 options, exactly one
    in-range correct_index, and non-empty prompt + explanation. Anything that
    can't be coerced to a valid MCQ is rejected (returns None) rather than
    stored.
    """
    if not isinstance(raw, dict):
        return None

    question = str(raw.get("question") or "").strip()
    explanation = str(raw.get("explanation") or "").strip()
    if not question or not explanation:
        return None

    options = raw.get("options")
    if not isinstance(options, list):
        return None
    options = [str(o).strip() for o in options if str(o).strip()]
    if not (_MIN_OPTIONS <= len(options) <= _MAX_OPTIONS):
        return None

    try:
        correct_index = int(raw.get("correct_index"))
    except (TypeError, ValueError):
        return None
    if not (0 <= correct_index < len(options)):
        return None

    return {
        "question": question,
        "options": options,
        "correct_index": correct_index,
        "explanation": explanation,
    }


def points_for(streak: int) -> int:
    """Points awarded for a correct answer given the current streak (count of
    consecutive prior correct answers). Base plus a per-streak bonus, clamped so
    a client-reported streak can never inflate the award without bound.

    Pure and deterministic: non-decreasing in streak, never above the clamp.
    """
    bonus = max(0, int(streak)) * STREAK_BONUS_PER
    return POINTS_PER_CORRECT + min(bonus, MAX_STREAK_BONUS)


def grade(question: dict, chosen_index: int, streak: int = 0) -> tuple[bool, int]:
    """Grade a multiple-choice answer. Deterministic, no LLM.

    Returns (correct, points). Points are awarded only when correct, scaled by
    the streak; an incorrect answer is always 0.
    """
    try:
        correct = int(chosen_index) == int(question.get("correct_index"))
    except (TypeError, ValueError):
        return False, 0
    return (True, points_for(streak)) if correct else (False, 0)


def summarize_mastery(results: list[dict]) -> dict:
    """Aggregate quiz result rows into per-topic mastery + a total.

    Each result row has at least: topic_slug, correct (bool), points (int).
    Returns {"topics": {slug: {answered, correct, correct_rate, points,
    mastered}}, "total_points": int}. Additive: more attempts never reduce
    recorded history.
    """
    topics: dict[str, dict] = {}
    total_points = 0
    for r in results:
        slug = r.get("topic_slug")
        if not slug:
            continue
        t = topics.setdefault(slug, {"answered": 0, "correct": 0, "points": 0})
        t["answered"] += 1
        if r.get("correct"):
            t["correct"] += 1
        pts = int(r.get("points") or 0)
        t["points"] += pts
        total_points += pts

    for t in topics.values():
        rate = t["correct"] / t["answered"] if t["answered"] else 0.0
        t["correct_rate"] = round(rate, 4)
        t["mastered"] = rate >= MASTERED_THRESHOLD

    return {"topics": topics, "total_points": total_points}


# --- LLM stages (generation + quality judge) -------------------------------

def _client():
    """Lazily construct the OpenAI client (keeps the module import-light)."""
    from openai import OpenAI
    import os
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def _strip_json(raw: str) -> str:
    """Strip markdown code fences the model sometimes wraps JSON in."""
    raw = (raw or "").strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()


def _build_question_prompt(topic_name: str, sections: list[dict], transcripts: list[str],
                           n: int = QUESTIONS_PER_TOPIC) -> str:
    """Pure: build the MCQ generation prompt. Instructs multiple-choice ONLY,
    substance-over-trivia, and curiosity/scenario phrasing so the quiz feels
    like a fun challenge rather than dry recall."""
    sections_block = "\n".join(
        f"- {s.get('title', '')}: {s.get('description', '')}" for s in (sections or [])
    ) or "(no section outline available)"
    excerpt = " ".join(t for t in (transcripts or []) if t).strip()[:_TRANSCRIPT_BUDGET]

    return f"""You are writing a short, fun multiple-choice quiz that tests whether someone UNDERSTOOD a lesson on "{topic_name}".

What the lesson covered:
{sections_block}

Source material (excerpts):
{excerpt}

Write {n} MULTIPLE-CHOICE questions that:
- test real UNDERSTANDING of the subject, NOT trivia about the video (never ask about the narrator, slide colors, phrasing, or what was "said")
- are phrased to spark curiosity — prefer scenario or "what happens if" framing over a dry "what is the definition of"
- each have EXACTLY ONE correct option and 2-3 plausible-but-wrong distractors
- include a one-line explanation of why the correct answer is right

Every question MUST be multiple-choice. Do NOT produce true/false or open-ended questions.

Return ONLY a JSON array:
[{{"question": "...", "options": ["...", "...", "..."], "correct_index": 0, "explanation": "..."}}]"""


def _generate_questions(topic_name: str, sections: list[dict], transcripts: list[str],
                        n: int = QUESTIONS_PER_TOPIC) -> list[dict]:
    """LLM: produce raw MCQ dicts for a topic. Raises on API/parse failure
    (callers handle that). Output is NOT yet validated/judged."""
    prompt = _build_question_prompt(topic_name, sections, transcripts, n)
    resp = _client().chat.completions.create(
        model=MODEL, max_tokens=1200, temperature=0.4,
        messages=[{"role": "user", "content": prompt}],
    )
    parsed = json.loads(_strip_json(resp.choices[0].message.content))
    return parsed if isinstance(parsed, list) else []


def _judge_question(topic_name: str, q: dict) -> dict:
    """LLM judge for a single MCQ. Returns {"ok": bool, "issue": str}. Rejects
    questions with multiple/no correct answers, a wrong correct_index, ambiguity,
    video-trivia, or throwaway distractors. Raises on API/parse failure."""
    payload = {k: q.get(k) for k in ("question", "options", "correct_index", "explanation")}
    prompt = f"""You are a strict quiz reviewer for a lesson on "{topic_name}". Evaluate this multiple-choice question.

{json.dumps(payload, indent=2)}

Mark "ok": false if ANY of these is true:
- more than one option is correct, or none is correct
- the marked correct_index is not actually the correct answer
- the question is ambiguous, or tests video trivia rather than understanding
- a distractor is an obvious throwaway (not plausible)

Return ONLY JSON: {{"ok": true, "issue": "<one line; empty when ok>"}}"""
    resp = _client().chat.completions.create(
        model=MODEL, max_tokens=200, temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    verdict = json.loads(_strip_json(resp.choices[0].message.content))
    return {"ok": bool(verdict.get("ok")), "issue": str(verdict.get("issue") or "")}


def _vet_questions(topic_name: str, raw_questions: list) -> list[dict]:
    """Validate then judge each raw question; keep only those that are valid
    MCQs AND pass the judge. Best-effort: a judge failure drops that question,
    never the batch."""
    kept: list[dict] = []
    for raw in raw_questions or []:
        q = _validate_question(raw)
        if q is None:
            continue
        try:
            verdict = _judge_question(topic_name, q)
        except Exception as exc:
            logger.warning(f"[quiz] judge failed for a '{topic_name}' question: {exc}")
            continue
        if verdict.get("ok"):
            kept.append(q)
    return kept


# --- orchestration (DB-touching, best-effort) ------------------------------

MAX_GENERATION_ATTEMPTS = 2    # batches to try before giving up on a topic


def generate_and_store_questions(topic_slug: str, topic_name: str) -> int:
    """Generation-time entrypoint: produce vetted MCQs for a topic and cache
    them. Idempotent (skips if questions already exist) and best-effort (any
    LLM/DB failure is logged and skipped, never raised) so it can never block
    clip delivery. Returns the number of questions stored.

    This is the only DB-touching function in the module; the judging/scoring
    logic above stays pure and unit-testable.
    """
    from app.db.supabase import get_client
    db = get_client()

    # Idempotent: skip topics that already have cached questions.
    try:
        existing = db.table("quiz_questions").select("id").eq("topic_slug", topic_slug).limit(1).execute()
        if existing.data:
            return 0
    except Exception as exc:
        logger.warning(f"[quiz] failed to check existing questions for '{topic_slug}': {exc}")
        return 0

    # Gather the section outline + a sample of clip transcripts as source.
    try:
        sec_res = (
            db.table("topic_sections")
            .select("title,description")
            .eq("topic_slug", topic_slug)
            .order("section_index")
            .execute()
        )
        sections = sec_res.data or []
    except Exception as exc:
        logger.warning(f"[quiz] failed to load sections for '{topic_slug}': {exc}")
        sections = []

    try:
        clip_res = (
            db.table("clips")
            .select("transcript")
            .eq("topic_slug", topic_slug)
            .limit(6)
            .execute()
        )
        transcripts = [c["transcript"] for c in (clip_res.data or []) if c.get("transcript")]
    except Exception as exc:
        logger.warning(f"[quiz] failed to load clips for '{topic_slug}': {exc}")
        transcripts = []

    if not sections and not transcripts:
        return 0  # nothing to generate from

    # Generate a pool (the judge rejects some) -> validate -> judge, retrying
    # the batch up to the bound. Keep up to QUESTIONS_PER_TOPIC that pass.
    kept: list[dict] = []
    for _ in range(MAX_GENERATION_ATTEMPTS):
        try:
            raw = _generate_questions(topic_name, sections, transcripts, n=GENERATION_POOL)
        except Exception as exc:
            logger.warning(f"[quiz] generation failed for '{topic_name}': {exc}")
            break
        kept = _vet_questions(topic_name, raw)[:QUESTIONS_PER_TOPIC]
        if kept:
            break

    if not kept:
        logger.info(f"[quiz] no questions passed the gate for '{topic_slug}'")
        return 0

    stored = 0
    for q in kept:
        try:
            db.table("quiz_questions").insert({"topic_slug": topic_slug, **q}).execute()
            stored += 1
        except Exception as exc:
            logger.warning(f"[quiz] failed to store a question for '{topic_slug}': {exc}")

    logger.info(f"[quiz] '{topic_slug}' stored {stored} question(s)")
    return stored
