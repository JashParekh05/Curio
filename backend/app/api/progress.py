"""Progress endpoint (Mastery_Compute + Soft_Unlock shell, Phase 3, Req 3).

The thin HTTP shell over ``services/progress.compute_progress``. It owns only the
auth glue and defers every decision to the pure cores via that shell.

- ``GET /api/progress/{subject}`` returns the learner's per-level
  :class:`~app.services.soft_unlock.LevelProgress` (with each level's per-topic
  :class:`~app.services.soft_unlock.TopicUnlock` status and mastery) for the
  progress bar and mastery badges. Owner-only, exactly like
  ``quiz.get_mastery``: the caller may only read their own progress (``subject``
  is the learner's ``user_id``), else a 403 is raised.

Best-effort: the underlying shell never raises into the response path (Req 4.3),
so the endpoint returns a well-formed (possibly empty) progress object even when
the additive Phase 3 ``learning_progress`` table is not yet present.

ASCII only.

Validates: Requirements 3.1, 3.3, 4.3, 5.4
"""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_user
from app.services import progress

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/progress", tags=["progress"])


@router.get("/{subject}")
async def get_progress(subject: str, caller_id: str = Depends(require_user)):
    """Per-level progress + per-topic unlock/mastery for the panel bar and badges.

    Owner-only, like ``quiz.get_mastery``: a caller may only read their own
    progress. Computation runs off the event loop (best-effort, never raises into
    the response) and the result is purely advisory -- nothing it returns can
    block feed progression (Req 3.3)."""
    if caller_id != subject:
        raise HTTPException(status_code=403, detail="Access denied")
    return await asyncio.to_thread(progress.compute_progress, subject)
