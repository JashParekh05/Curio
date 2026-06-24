"""Game_Router for the Adaptive Learning Game (Req 13).

This module defines the wire contract (Pydantic request/response models) for the
three game endpoints and the ``APIRouter`` scaffold. The endpoint handlers are
implemented in task 5.2 and the router is registered in ``main.py`` in task 5.3.
"""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth import require_user
from app.services import game as game_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/game", tags=["game"])


# ---- Request/response models (design "Pydantic schemas") --------------------


class StartSessionRequest(BaseModel):
    topic: str  # rejected upstream if blank (Req 1.3)


class ProbeQuestion(BaseModel):
    question: str
    options: list[str]
    correct_index: int
    explanation: str
    concept_tag: str  # Req 2.3, 4
    level: str  # "prerequisite" | "core" | "stretch"


class SessionStartResponse(BaseModel):
    session_id: str
    goal: str  # the Goal_Node (Req 1.1)
    current_node: str  # == goal at start
    probe: list[ProbeQuestion]  # exactly 6 (Req 2.1)


class GradedAnswer(BaseModel):
    concept_tag: str
    correct: bool  # client pre-grades; server re-derives via quiz.grade


class DecideRequest(BaseModel):
    goal: str
    current_node: str
    path: list[str]  # nodes already visited (Req 6.3)
    questions: list[ProbeQuestion]  # the quiz just taken
    answers: list[int]  # chosen option index per question


class DecideResponse(BaseModel):
    action: Literal["DESCEND", "REINFORCE", "CLIMB"]
    band: Literal["DESCEND", "REINFORCE", "CLIMB"]
    next_node: str | None
    reached_goal: bool
    gap: str
    diagnosis: str  # shown on the outcome card (Req 20.4)
    score_pct: float
    missed_concepts: list[str]
    correct_concepts: list[str]


class NodeRequest(BaseModel):
    node: str
    goal: str


class NodeClip(BaseModel):
    video_id: str
    video_url: str  # youtube.com/embed/... (Req 10.3)
    title: str
    channel_title: str | None
    duration_seconds: int
    has_caption: bool
    view_count: int
    thumbnail_url: str | None
    description: str | None


class NodeResponse(BaseModel):
    node: str
    hook: str  # Intuition_Card (Req 9)
    clip: NodeClip | None  # None when no clip affordable (Req 10.4)
    quiz: list[ProbeQuestion]  # 3 validated MCQs (Req 11.3)


class PathsResponse(BaseModel):
    # Additive Phase 2 branching hook (Req 14.1, 14.3): a small set of valid,
    # band-consistent, not-in-path candidate next nodes the engine could advance
    # to. May be empty/short, in which case the client falls back to the single
    # engine-chosen node. Does NOT alter /decide or /node behavior.
    candidates: list[str]


class LocalProgressNode(BaseModel):
    node: str
    state: str  # LOCKED | NEXT | CLEARED | DECAYING | MASTERED (Req 19.3)
    decay_due_at: str | None = None
    decay_step: int = 0


class ResumeRequest(BaseModel):
    session_id: str
    # The client's local-only state, preferred only when nothing is persisted
    # server-side (Req 17.2).
    goal: str | None = None
    floor_node: str | None = None
    nodes: list[LocalProgressNode] = []


class ResumeResponse(BaseModel):
    # "persisted" when server-side progress won, "local" when the client's
    # local-only state was kept (Req 17.2).
    source: Literal["persisted", "local"]
    session_id: str
    goal: str | None
    floor_node: str | None
    nodes: list[LocalProgressNode]


# ---- Service <-> wire mapping helpers ---------------------------------------


def _to_probe_question(q: dict) -> ProbeQuestion:
    """Map a Game_Service question dict to the wire ProbeQuestion model.

    Probe questions carry a ``level`` (Req 2.3); node Checkpoint_Quiz questions
    do not (Req 11.3), so ``level`` defaults to an empty string for those.
    """
    return ProbeQuestion(
        question=q.get("question", ""),
        options=list(q.get("options") or []),
        correct_index=int(q.get("correct_index") or 0),
        explanation=q.get("explanation", ""),
        concept_tag=q.get("concept_tag", ""),
        level=q.get("level", ""),
    )


def _to_node_clip(clip: dict | None) -> NodeClip | None:
    """Map a Game_Service clip dict to the wire NodeClip model, or None."""
    if clip is None:
        return None
    return NodeClip(
        video_id=clip.get("video_id", ""),
        video_url=clip.get("video_url", ""),
        title=clip.get("title", ""),
        channel_title=clip.get("channel_title"),
        duration_seconds=int(clip.get("duration_seconds") or 0),
        has_caption=bool(clip.get("has_caption")),
        view_count=int(clip.get("view_count") or 0),
        thumbnail_url=clip.get("thumbnail_url"),
        description=clip.get("description"),
    )


# ---- Endpoints (each depends on the guest token, Req 13.6) ------------------


@router.post("/session", response_model=SessionStartResponse)
async def start_session(
    body: StartSessionRequest,
    caller_id: str = Depends(require_user),
) -> SessionStartResponse:
    """Start a session and return its 6-question placement Probe (Req 13.3, 1, 2).

    A blank topic is rejected with HTTP 400 (Req 1.3). When the probe cannot be
    produced as 6 valid MCQs, ``start_session`` raises ``ProbeGenerationError``,
    mapped here to HTTP 502 ``probe_generation_failed`` (Req 2.5).
    """
    if not body.topic or not body.topic.strip():
        raise HTTPException(status_code=400, detail="topic must not be blank")

    try:
        result = game_service.start_session(body.topic)
    except game_service.ProbeGenerationError:
        raise HTTPException(status_code=502, detail="probe_generation_failed")

    # Associate the session with the caller's user id (Req 17.1). Best-effort:
    # persist_session swallows Supabase failures so a persistence outage never
    # blocks starting the session (Req 16.2, 16.3).
    game_service.persist_session(
        session_id=result.session_id,
        user_id=caller_id,
        goal=result.goal,
        floor_node=None,
    )

    return SessionStartResponse(
        session_id=result.session_id,
        goal=result.goal,
        current_node=result.current_node,
        probe=[_to_probe_question(q) for q in result.probe],
    )


@router.post("/decide", response_model=DecideResponse)
async def decide(
    body: DecideRequest,
    caller_id: str = Depends(require_user),
) -> DecideResponse:
    """Grade answers, band, and return the next-step decision (Req 13.4, 3-6, 8).

    The wire ``questions`` carry the Pydantic ProbeQuestion shape; the service
    DecideRequest takes plain dicts, so they are converted here. When the
    ``decide_next`` LLM call cannot produce a usable decision the service raises
    ``DecideError``, mapped to HTTP 502 ``decide_failed`` (Req 5.11).
    """
    service_req = game_service.DecideRequest(
        goal=body.goal,
        current_node=body.current_node,
        path=list(body.path),
        questions=[q.model_dump() for q in body.questions],
        answers=list(body.answers),
    )

    try:
        result = game_service.decide(service_req)
    except game_service.DecideError:
        raise HTTPException(status_code=502, detail="decide_failed")

    return DecideResponse(
        action=result.action,
        band=result.band,
        next_node=result.next_node,
        reached_goal=result.reached_goal,
        gap=result.gap,
        diagnosis=result.diagnosis,
        score_pct=result.score_pct,
        missed_concepts=list(result.missed_concepts),
        correct_concepts=list(result.correct_concepts),
    )


@router.post("/node", response_model=NodeResponse)
async def deliver_node(
    body: NodeRequest,
    caller_id: str = Depends(require_user),
) -> NodeResponse:
    """Return a node's Intuition_Card + Clip + Checkpoint_Quiz (Req 13.5, 7, 9-11)."""
    result = game_service.deliver_node(body.node, body.goal)

    return NodeResponse(
        node=result.node,
        hook=result.hook,
        clip=_to_node_clip(result.clip),
        quiz=[_to_probe_question(q) for q in result.quiz],
    )


@router.get("/paths", response_model=PathsResponse)
async def candidate_paths(
    goal: str = Query(..., description="The Goal_Node (entered topic)"),
    current_node: str = Query(..., description="The node the learner is on"),
    path: list[str] = Query(
        default_factory=list, description="Nodes already visited this session"
    ),
    caller_id: str = Depends(require_user),
) -> PathsResponse:
    """Return 2-3 valid candidate next nodes for learner-chosen branching (Req 14.1, 14.3).

    Additive, read-only Phase 2 hook. It does NOT change the behavior of
    ``/session``, ``/decide``, or ``/node`` — it reuses the existing CLIMB
    decision logic (``game.candidate_paths`` → ``decide_next``) to surface
    alternative on-goal next Stages the engine could advance to, so the
    Play_Surface can offer them as forks (Req 14.1). Candidates are guaranteed
    valid, band-consistent, not already in ``path``, and never past the goal
    (Req 14.3). The helper is best-effort: when it can't produce candidates the
    list is empty/short and the client falls back to the single-path flow, so
    this endpoint never errors the loop.
    """
    candidates = game_service.candidate_paths(
        goal=goal,
        current_node=current_node,
        path=list(path),
    )
    return PathsResponse(candidates=candidates)


@router.post("/resume", response_model=ResumeResponse)
async def resume_session(
    body: ResumeRequest,
    caller_id: str = Depends(require_user),
) -> ResumeResponse:
    """Resume a session, preferring persisted progress over local state (Req 16.4, 17).

    Associates the resume with the caller's user id from ``require_user``
    (Req 17.1) and loads the learner's persisted progress from Supabase
    best-effort (Req 16.4). WHERE persisted progress exists, it is preferred over
    the client's local-only state (Req 17.2); otherwise the local-only state in
    the request is returned unchanged so an offline-only or brand-new session is
    never clobbered. The load is best-effort: a Supabase outage degrades to
    returning the local-only state rather than erroring (Req 16.2, 16.3).
    """
    persisted = game_service.load_resume_state(body.session_id)
    local = {
        "session_id": body.session_id,
        "user_id": caller_id,
        "goal": body.goal,
        "floor_node": body.floor_node,
        "nodes": [n.model_dump() for n in body.nodes],
    }
    resolved = game_service.prefer_persisted_progress(persisted, local)

    return ResumeResponse(
        source=resolved["source"],
        session_id=resolved.get("session_id") or body.session_id,
        goal=resolved.get("goal"),
        floor_node=resolved.get("floor_node"),
        nodes=[LocalProgressNode(**n) for n in resolved.get("nodes") or []],
    )
