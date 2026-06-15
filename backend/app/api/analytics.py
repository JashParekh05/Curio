import asyncio
import logging

from fastapi import APIRouter, Depends

from app.auth import require_user
from app.services.analytics import topic_dropoff

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/dropoff/{topic_slug}")
async def get_topic_dropoff(topic_slug: str, caller_id: str = Depends(require_user)):
    """Per-beat retention funnel for a topic: views, completion rate, average
    watch ratio, and retention relative to the first beat. Read-only."""
    funnel = await asyncio.to_thread(topic_dropoff, topic_slug)
    return {"topic_slug": topic_slug, "funnel": funnel}
