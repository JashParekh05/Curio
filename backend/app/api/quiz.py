import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import require_user
from app.db.supabase import get_client
from app.services.quiz import grade, points_for, summarize_mastery

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/quiz", tags=["quiz"])


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
async def get_quiz(topic_slug: str, caller_id: str = Depends(require_user)):
    """Cached MCQs for a topic, including correct_index + explanation so the
    client can grade and reveal instantly. Empty list when none generated yet."""
    return await asyncio.to_thread(_topic_questions, topic_slug)


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
