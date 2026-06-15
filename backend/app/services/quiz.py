"""Active-learning quiz: pure helpers for the multiple-choice quiz feature.

This module hosts the deterministic, unit-testable core — question validation,
grading, streak points, and mastery aggregation. The LLM generation/judge stages
and the DB-touching orchestration are added on top (later tasks); they stay thin
so this logic carries the test rigor used across the codebase.

Design ref: .kiro/specs/active-learning-quiz/design.md
"""
import logging

logger = logging.getLogger(__name__)

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
