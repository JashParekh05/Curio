from fastapi import APIRouter, Query
from app.models.schemas import Clip, FeedResponse
from app.db.supabase import get_client

router = APIRouter(prefix="/api/feed", tags=["feed"])


@router.get("/{topic_slug}", response_model=FeedResponse)
async def get_feed(
    topic_slug: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=50),
):
    db = get_client()
    result = (
        db.table("clips")
        .select("*")
        .eq("topic_slug", topic_slug)
        .range(offset, offset + limit - 1)
        .execute()
    )

    clips = [Clip(**row) for row in result.data]

    # If no clips yet, the worker is still processing
    processing = len(clips) == 0

    return FeedResponse(
        topic_slug=topic_slug,
        clips=clips,
        processing=processing,
    )


@router.get("/path/{session_id}", response_model=list[FeedResponse])
async def get_path_feed(session_id: str):
    """Return clips for every topic in a learning path, in order."""
    db = get_client()
    path = (
        db.table("learning_paths")
        .select("topic_slugs")
        .eq("session_id", session_id)
        .single()
        .execute()
    )

    feeds = []
    for slug in path.data["topic_slugs"]:
        result = (
            db.table("clips")
            .select("*")
            .eq("topic_slug", slug)
            .limit(10)
            .execute()
        )
        clips = [Clip(**row) for row in result.data]
        feeds.append(
            FeedResponse(
                topic_slug=slug,
                clips=clips,
                processing=len(clips) == 0,
            )
        )

    return feeds
