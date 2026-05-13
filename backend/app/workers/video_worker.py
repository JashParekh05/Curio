import os
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

celery = Celery("learnreel", broker=os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
celery.conf.task_serializer = "json"
celery.conf.result_backend = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


@celery.task(bind=True, max_retries=2)
def process_video_task(self, video_url: str, topic_slug: str):
    from app.services.pipeline import process_video
    from app.services.firecrawl import search_videos
    from app.db.supabase import get_client

    try:
        clips = process_video(video_url, topic_slug)
        db = get_client()
        for clip in clips:
            db.table("clips").insert(clip).execute()
        return {"status": "done", "clips": len(clips)}
    except Exception as exc:
        raise self.retry(exc=exc, countdown=30)


@celery.task
def discover_and_process_topic(topic_slug: str, topic_name: str):
    """Find videos for a topic then queue each one for processing."""
    from app.services.firecrawl import search_videos

    videos = search_videos(topic_name, max_results=5)
    for video in videos[:3]:  # process top 3 to start
        process_video_task.delay(video["url"], topic_slug)

    return {"status": "queued", "videos": len(videos)}
