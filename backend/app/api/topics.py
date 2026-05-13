from fastapi import APIRouter
from app.models.schemas import TopicRequest, LearningPath
from app.services.llm import parse_learning_path
from app.db.supabase import get_client
from app.workers.video_worker import discover_and_process_topic

router = APIRouter(prefix="/api/topics", tags=["topics"])


@router.post("/", response_model=LearningPath)
async def create_learning_path(req: TopicRequest):
    path = parse_learning_path(req.query, req.session_id)

    db = get_client()
    db.table("learning_paths").insert(
        {
            "session_id": path.session_id,
            "user_query": path.user_query,
            "topic_slugs": [t.slug for t in path.topics],
        }
    ).execute()

    for topic in path.topics:
        existing = (
            db.table("topics")
            .select("slug")
            .eq("slug", topic.slug)
            .execute()
        )
        if not existing.data:
            db.table("topics").insert(
                {
                    "slug": topic.slug,
                    "name": topic.name,
                    "difficulty": topic.difficulty,
                    "prerequisites": topic.prerequisites,
                }
            ).execute()

        # Check if we already have clips; if not, queue discovery
        clips = (
            db.table("clips")
            .select("id")
            .eq("topic_slug", topic.slug)
            .limit(1)
            .execute()
        )
        if not clips.data:
            discover_and_process_topic.delay(topic.slug, topic.name)

    return path
