import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from app.auth import require_user
from app.db.supabase import get_client
from app.services import stage_anchor
from app.services.quiz import generate_and_store_questions, grade, points_for, summarize_mastery

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/quiz", tags=["quiz"])

# Topics currently self-healing a quiz in the background, so concurrent GETs
# don't kick off duplicate generations. Best-effort de-dupe within one process.
_quiz_generating: set[str] = set()


class QuizAnswer(BaseModel):
    chosen_index: int
    session_id: str | None = None
    streak: int = 0


def _topic_questions(topic_slug: str) -> list[dict]:
    db = get_client()
    try:
        res = (
            db.table("quiz_questions")
            .select("id,question,options,correct_index,explanation")
            .eq("topic_slug", topic_slug)
            .execute()
        )
    except Exception as e:
        logger.warning(f"[quiz] failed to load questions for '{topic_slug}': {e}")
        return []
    return res.data or []


def _anchored_topic_questions(topic_slug: str, anchor: stage_anchor.QuestionAnchor) -> list[dict]:
    """Cached MCQs for a topic, scoped to a normalized stage/section anchor.

    A ``check`` is beat-anchored, so its questions are filtered to the matching
    ``(stage, section_index)`` beat; a ``pre`` / ``post`` is topic-wide, so only
    the ``stage`` is matched (``section_index`` is ``None`` for those). Selects
    the same columns as :func:`_topic_questions` so the client grades/reveals
    identically.

    Graceful (Req 4.3, 5.3): the ``stage`` / ``section_index`` columns are
    additive (Phase 2 migration) and may be absent in the DB. When the filtered
    select errors (columns missing), this degrades to the existing topic-wide
    read so nothing breaks and the endpoint stays backward compatible.
    """
    db = get_client()
    try:
        q = (
            db.table("quiz_questions")
            .select("id,question,options,correct_index,explanation")
            .eq("topic_slug", topic_slug)
            .eq("stage", anchor.stage)
        )
        if anchor.section_index is not None:
            q = q.eq("section_index", anchor.section_index)
        res = q.execute()
    except Exception as e:
        logger.warning(
            f"[quiz] anchored question load failed for '{topic_slug}' "
            f"(stage/section_index columns may be absent); degrading to "
            f"topic-wide read: {e}"
        )
        return _topic_questions(topic_slug)

    rows = res.data or []
    if rows:
        return rows

    # No questions match this exact (stage, section_index) anchor yet -- the
    # topic's quizzes were generated topic-wide (the live self-heal path) rather
    # than per-beat, so a strict anchor match is empty. Rather than show a blank
    # checkpoint card, fall back to the topic's available questions so the card is
    # always populated (Req 4.3). Beat-anchored generation, when it runs, will
    # narrow this to that beat's questions.
    return _topic_questions(topic_slug)


def _topic_has_clips(topic_slug: str) -> bool:
    """True when the topic has at least one clip (i.e. it's worth quizzing)."""
    db = get_client()
    try:
        res = db.table("clips").select("id").eq("topic_slug", topic_slug).limit(1).execute()
        return bool(res.data)
    except Exception as e:
        logger.warning(f"[quiz] failed clip check for '{topic_slug}': {e}")
        return False


def _topic_name(topic_slug: str) -> str:
    """Human-readable topic name, falling back to a slug-derived title."""
    db = get_client()
    try:
        res = db.table("topics").select("name").eq("slug", topic_slug).limit(1).execute()
        if res.data and res.data[0].get("name"):
            return res.data[0]["name"]
    except Exception as e:
        logger.warning(f"[quiz] failed name lookup for '{topic_slug}': {e}")
    return topic_slug.replace("-", " ").title()


def _bg_generate_quiz(topic_slug: str) -> None:
    """Background self-heal: generate+store a quiz for a topic that has clips
    but no cached questions, then release the in-flight guard. Best-effort."""
    try:
        name = _topic_name(topic_slug)
        generate_and_store_questions(topic_slug, name)
    except Exception as e:
        logger.warning(f"[quiz] self-heal generation failed for '{topic_slug}': {e}")
    finally:
        _quiz_generating.discard(topic_slug)


def _maybe_self_heal(topic_slug: str, background_tasks: BackgroundTasks) -> None:
    """If a topic has clips but no quiz yet, kick off generation in the
    background so the quiz appears automatically on a later poll/open. Guarded
    so only one generation runs per topic at a time."""
    if topic_slug in _quiz_generating:
        return
    if not _topic_has_clips(topic_slug):
        return
    _quiz_generating.add(topic_slug)
    background_tasks.add_task(_bg_generate_quiz, topic_slug)


@router.get("/mastery/{user_id}")
async def get_mastery(user_id: str, caller_id: str = Depends(require_user)):
    """Per-topic mastery summary + total points for the panel header/pips."""
    if caller_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    db = get_client()
    try:
        res = db.table("quiz_results").select("topic_slug,correct,points").eq("user_id", user_id).execute()
        rows = res.data or []
    except Exception as e:
        logger.warning(f"[quiz] failed to load mastery for user={user_id}: {e}")
        rows = []
    return summarize_mastery(rows)


@router.get("/{topic_slug}")
async def get_quiz(
    topic_slug: str,
    background_tasks: BackgroundTasks,
    stage: str | None = None,
    section_index: int | None = None,
    caller_id: str = Depends(require_user),
):
    """Cached MCQs for a topic, including correct_index + explanation so the
    client can grade and reveal instantly. When none exist yet but the topic
    has clips, kick off generation in the background (lazy self-heal) so the
    quiz shows up automatically on a later poll. Empty list in the meantime.

    Stage anchoring (Stage_Anchor shell, Req 2.2): when ``stage`` is supplied,
    the anchor is normalized via ``stage_anchor.normalize_anchor`` and the
    returned questions are scoped to it -- a ``check`` returns that beat's
    questions (``section_index`` 0..3), while a ``pre`` / ``post`` returns the
    topic-wide questions for that stage. When the additive ``stage`` /
    ``section_index`` columns are absent, the read degrades to the topic-wide
    behavior (Req 4.3).

    Default behavior (no ``stage`` query param) is unchanged: every cached
    question for the topic is returned exactly as before (backward compatible).
    """
    if stage is None:
        questions = await asyncio.to_thread(_topic_questions, topic_slug)
    else:
        anchor = stage_anchor.normalize_anchor(stage, section_index)
        questions = await asyncio.to_thread(_anchored_topic_questions, topic_slug, anchor)
    if not questions:
        await asyncio.to_thread(_maybe_self_heal, topic_slug, background_tasks)
    return questions


@router.post("/{question_id}/answer", status_code=204)
async def answer_question(question_id: str, body: QuizAnswer, caller_id: str = Depends(require_user)):
    """Record an answer. The client already graded/revealed; this recomputes
    correctness server-side (spoof-proof), awards points once per (question,
    user) on a first correct answer, and stores the result. Best-effort."""
    db = get_client()
    try:
        q = (
            db.table("quiz_questions")
            .select("topic_slug,options,correct_index")
            .eq("id", question_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.warning(f"[quiz] failed to load question {question_id}: {e}")
        return
    if not q.data:
        return
    question = q.data[0]

    correct, points = grade(question, body.chosen_index, body.streak)

    # Points once: if this user already answered this question correctly, award 0.
    if correct and points:
        try:
            prior = (
                db.table("quiz_results")
                .select("id")
                .eq("question_id", question_id)
                .eq("user_id", caller_id)
                .eq("correct", True)
                .limit(1)
                .execute()
            )
            if prior.data:
                points = 0
        except Exception as e:
            logger.warning(f"[quiz] prior-answer check failed for {question_id}: {e}")

    try:
        db.table("quiz_results").insert({
            "question_id": question_id,
            "topic_slug": question.get("topic_slug"),
            "user_id": caller_id,
            "session_id": body.session_id,
            "chosen_index": body.chosen_index,
            "correct": correct,
            "points": points,
        }).execute()
    except Exception as e:
        logger.warning(f"[quiz] failed to record answer for {question_id}: {e}")
    return
