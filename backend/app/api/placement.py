"""Placement endpoints (Placement_Score_Map shell, Phase 2, Req 2.1/4.2/4.3).

This is the thin, best-effort I/O shell paired with the pure decision cores
``placement_score.score_to_level`` (diagnostic score -> starting Content_Level)
and the existing ``placement.place`` (starting level -> entry node on the leveled
path). It owns the DB/auth glue and decides nothing itself:

- ``GET /api/placement/{session_id}/questions`` returns ~5-8 ``pre`` diagnostic
  questions for the session's path, lazily self-healing (like ``quiz.get_quiz``)
  by kicking off ``quiz.generate_and_store_questions(stage="pre")`` in the
  background when none are cached yet.
- ``POST /api/placement/{session_id}`` grades the diagnostic (server-side via
  ``quiz.grade`` over submitted answers, or accepts ``correct``/``total`` counts,
  or a ``skip`` flag), maps the result through ``placement_score.score_to_level``
  (loading the learner's ``grade_level`` from ``user_profiles`` as the fallback
  floor), then calls the existing ``placement.place`` over the session's spine
  path to choose ``entry_index``, returning ``{starting_level, entry_index,
  est_minutes}``.

Following the codebase convention, every external call is wrapped so a failure
degrades to a safe default and never raises into the response path (Req 4.2,
4.3). The diagnostic is always optional: a ``skip`` (or an empty/failed grade)
defers entirely to ``grade_level``.

ASCII only.
"""
import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from app.auth import require_user
from app.db.supabase import get_client
from app.services import placement, placement_score
from app.services.quiz import generate_and_store_questions, grade

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/placement", tags=["placement"])

#: How many diagnostic questions to surface for a placement quiz (lightweight).
_MAX_DIAGNOSTIC_QUESTIONS = 8

#: The quiz stage diagnostics are stored under (topic-wide, pre-feed).
_PRE_STAGE = "pre"

# Sessions currently self-healing a placement quiz in the background, so
# concurrent GETs don't kick off duplicate generations. Best-effort, per-process.
_placement_generating: set[str] = set()


class PlacementAnswer(BaseModel):
    question_id: str
    chosen_index: int


class PlacementSubmission(BaseModel):
    """Body for ``POST /api/placement/{session_id}``.

    A diagnostic result may be supplied three ways (in priority order):
    1. ``skip=True`` -> no diagnostic taken, defer to ``grade_level``.
    2. ``answers`` -> graded server-side via ``quiz.grade`` (spoof-proof).
    3. ``correct`` + ``total`` -> pre-counted correctness.

    When nothing usable is supplied the placement defers to ``grade_level``
    (total of 0), exactly like a skip.
    """

    skip: bool = False
    answers: list[PlacementAnswer] | None = None
    correct: int | None = None
    total: int | None = None


class PlacementResultResponse(BaseModel):
    starting_level: str
    entry_index: int | None
    est_minutes: int


# ---------------------------------------------------------------------------
# Shared helpers (best-effort, off the request path)
# ---------------------------------------------------------------------------

def _load_path(session_id: str) -> dict | None:
    """Load the learning path row (slugs, query, owner) for a session, or None."""
    db = get_client()
    try:
        res = (
            db.table("learning_paths")
            .select("topic_slugs, user_query, user_id")
            .eq("session_id", session_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.warning(f"[placement] failed to load path for session={session_id}: {e}")
        return None
    return res.data[0] if res.data else None


def _topic_names(slugs: list[str]) -> dict[str, str]:
    """Best-effort human-readable names for a set of slugs (slug-title fallback)."""
    names: dict[str, str] = {}
    if not slugs:
        return names
    db = get_client()
    try:
        rows = db.table("topics").select("slug,name").in_("slug", slugs).execute()
        names = {r["slug"]: r["name"] for r in (rows.data or []) if r.get("name")}
    except Exception as e:
        logger.warning(f"[placement] name lookup failed: {e}")
    for slug in slugs:
        names.setdefault(slug, slug.replace("-", " ").title())
    return names


def _pre_questions(slugs: list[str]) -> list[dict]:
    """Return up to ``_MAX_DIAGNOSTIC_QUESTIONS`` cached ``pre`` questions across
    the path's topics. Degrades to [] when the additive ``stage`` column is not
    yet present (operator-run migration) so a missing column never breaks the
    diagnostic screen."""
    if not slugs:
        return []
    db = get_client()
    try:
        res = (
            db.table("quiz_questions")
            .select("id,question,options,correct_index,explanation")
            .in_("topic_slug", slugs)
            .eq("stage", _PRE_STAGE)
            .limit(_MAX_DIAGNOSTIC_QUESTIONS)
            .execute()
        )
        return res.data or []
    except Exception as e:
        logger.warning(f"[placement] failed to load pre questions (stage column may be absent): {e}")
        return []


def _bg_generate_diagnostics(session_id: str, slugs: list[str]) -> None:
    """Background self-heal: generate ``pre`` diagnostics for the path's leading
    topics so the placement quiz appears on a later poll. Best-effort."""
    try:
        names = _topic_names(slugs)
        # Lightweight: a few leading topics yield enough diagnostic coverage.
        for slug in slugs[:3]:
            try:
                generate_and_store_questions(slug, names.get(slug, slug), stage=_PRE_STAGE)
            except Exception as e:
                logger.warning(f"[placement] pre generation failed for '{slug}': {e}")
    finally:
        _placement_generating.discard(session_id)


def _maybe_self_heal(session_id: str, slugs: list[str], background_tasks: BackgroundTasks) -> None:
    """Kick off diagnostic generation once per session when none are cached."""
    if session_id in _placement_generating or not slugs:
        return
    _placement_generating.add(session_id)
    background_tasks.add_task(_bg_generate_diagnostics, session_id, slugs)


def _grade_answers(answers: list["PlacementAnswer"]) -> tuple[int, int]:
    """Grade submitted diagnostic answers server-side via ``quiz.grade``.

    Loads each question's ``correct_index`` and counts correct answers. Returns
    ``(correct, total)`` where ``total`` is the number of answers that resolved to
    a real question. Best-effort: a failed lookup simply isn't counted."""
    if not answers:
        return 0, 0
    db = get_client()
    ids = [a.question_id for a in answers]
    by_id: dict[str, dict] = {}
    try:
        res = (
            db.table("quiz_questions")
            .select("id,correct_index")
            .in_("id", ids)
            .execute()
        )
        by_id = {r["id"]: r for r in (res.data or [])}
    except Exception as e:
        logger.warning(f"[placement] failed to load diagnostic questions for grading: {e}")
        return 0, 0

    correct = 0
    total = 0
    for a in answers:
        q = by_id.get(a.question_id)
        if not q:
            continue
        total += 1
        is_correct, _ = grade(q, a.chosen_index)
        if is_correct:
            correct += 1
    return correct, total


def _grade_level_for(path_row: dict) -> str | None:
    """Load the learner's ``grade_level`` from ``user_profiles`` (the fallback
    floor when no diagnostic was taken). None when unavailable."""
    user_id = path_row.get("user_id")
    if not user_id:
        return None
    db = get_client()
    try:
        res = (
            db.table("user_profiles")
            .select("grade_level")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0].get("grade_level")
    except Exception as e:
        logger.warning(f"[placement] failed to load grade_level for user={user_id}: {e}")
    return None


def _spine_nodes_for(user_query: str) -> list[placement.SpineNode]:
    """Resolve the session's leveled path to placement Spine_Nodes.

    Reuses the existing ``spine_router_runner.resolve_path`` (the same ordered,
    prerequisite-respecting spine path the feed is built from) and maps each node
    to a ``placement.SpineNode``. Best-effort: any failure yields an empty path so
    placement degrades to "not possible" rather than raising."""
    if not user_query:
        return []
    try:
        from app.services.spine_router_runner import resolve_path

        result = resolve_path(user_query)
        return [
            placement.SpineNode(
                topic_slug=getattr(n, "topic_slug", ""),
                content_level=getattr(n, "content_level", None) or "",
                est_minutes=int(getattr(n, "est_minutes", 0) or 0),
            )
            for n in result.path
        ]
    except Exception as e:
        logger.warning(f"[placement] failed to resolve spine path: {e}")
        return []


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/{session_id}/questions")
async def get_placement_questions(
    session_id: str,
    background_tasks: BackgroundTasks,
    caller_id: str = Depends(require_user),
):
    """Return ~5-8 ``pre`` diagnostic questions for the session's path. When none
    are cached yet, kick off generation in the background (lazy self-heal) so the
    diagnostic shows up on a later poll. Empty list in the meantime; the
    diagnostic is always optional (the client may skip)."""
    path_row = await asyncio.to_thread(_load_path, session_id)
    if not path_row:
        return []
    if path_row.get("user_id") and path_row["user_id"] != caller_id:
        raise HTTPException(status_code=403, detail="Access denied")

    slugs = path_row.get("topic_slugs") or []
    questions = await asyncio.to_thread(_pre_questions, slugs)
    if not questions:
        await asyncio.to_thread(_maybe_self_heal, session_id, slugs, background_tasks)
    return questions


@router.post("/{session_id}", response_model=PlacementResultResponse)
async def submit_placement(
    session_id: str,
    body: PlacementSubmission,
    caller_id: str = Depends(require_user),
):
    """Grade the diagnostic, map it to a starting Content_Level, and place the
    learner on the leveled path.

    Grading priority: ``skip`` -> defer to ``grade_level`` (total 0); else graded
    ``answers`` (server-side, spoof-proof); else supplied ``correct``/``total``
    counts; else (nothing usable) defer to ``grade_level``. The result feeds
    ``placement_score.score_to_level`` then the existing ``placement.place`` to
    choose ``entry_index``. Never raises into the response (Req 4.2, 4.3)."""
    path_row = await asyncio.to_thread(_load_path, session_id)
    if not path_row:
        raise HTTPException(status_code=404, detail="Session not found")
    if path_row.get("user_id") and path_row["user_id"] != caller_id:
        raise HTTPException(status_code=403, detail="Access denied")

    grade_level = await asyncio.to_thread(_grade_level_for, path_row)

    # Resolve the diagnostic result (correct, total). A skip or absent diagnostic
    # leaves total at 0 so score_to_level defers entirely to grade_level.
    correct, total = 0, 0
    if not body.skip:
        if body.answers:
            correct, total = await asyncio.to_thread(_grade_answers, body.answers)
        elif body.total is not None and body.total > 0:
            total = body.total
            correct = body.correct or 0

    starting_level = placement_score.score_to_level(correct, total, grade_level)

    # Place the learner on the session's leveled path. Edges are retained for
    # interface compatibility; the spine path is already prerequisite-ordered, so
    # level-based entry selection is itself prerequisite-consistent (edges=[]).
    nodes = await asyncio.to_thread(_spine_nodes_for, path_row.get("user_query") or "")
    result = placement.place(nodes, [], starting_level)

    return PlacementResultResponse(
        starting_level=starting_level,
        entry_index=result.entry_index,
        est_minutes=result.est_minutes,
    )
