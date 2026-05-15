"""
One-time seeding script. Reads seed/curated_topics.json, runs the audio→Whisper→Groq
pipeline for each video, and inserts topics + clips into Supabase.

Idempotent: skips topics that already have clips.

Usage:
    cd backend
    python -m scripts.seed_clips                 # process all topics
    python -m scripts.seed_clips neural-networks-basics binary-search    # specific topics only
"""
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from app.db.supabase import get_client
from app.services.pipeline import process_video

logger = logging.getLogger("seed_clips")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

SEED_PATH = Path(__file__).resolve().parent.parent / "seed" / "curated_topics.json"


def main(slug_filter: list[str] | None = None) -> None:
    db = get_client()
    data = json.loads(SEED_PATH.read_text())
    topics = data["topics"]

    if slug_filter:
        topics = {k: v for k, v in topics.items() if k in slug_filter}
        if not topics:
            logger.error(f"No matching topics in seed file. Available: {list(data['topics'].keys())}")
            sys.exit(1)

    inserted_topics = 0
    inserted_clips = 0
    skipped_topics = 0

    for slug, meta in topics.items():
        existing = (
            db.table("clips").select("id").eq("topic_slug", slug).limit(1).execute()
        )
        if existing.data:
            logger.info(f"[{slug}] already has clips, skipping")
            skipped_topics += 1
            continue

        topic_row = db.table("topics").select("slug").eq("slug", slug).execute()
        if not topic_row.data:
            db.table("topics").insert({
                "slug": slug,
                "name": meta["name"],
                "difficulty": meta.get("difficulty", "beginner"),
                "prerequisites": meta.get("prerequisites", []),
            }).execute()
            inserted_topics += 1

        for video_url in meta["videos"]:
            import time
            time.sleep(3)
            try:
                clips = process_video(video_url, slug)
            except Exception as e:
                logger.warning(f"[{slug}] {video_url} failed: {e}")
                continue

            for clip in clips:
                try:
                    db.table("clips").insert(clip).execute()
                    inserted_clips += 1
                except Exception as e:
                    logger.warning(f"[{slug}] clip insert failed: {e}")

            logger.info(f"[{slug}] inserted {len(clips)} clips from {video_url}")

    logger.info(
        f"Done. Topics inserted: {inserted_topics}, clips inserted: {inserted_clips}, "
        f"topics skipped (already had clips): {skipped_topics}"
    )


if __name__ == "__main__":
    slug_filter = sys.argv[1:] if len(sys.argv) > 1 else None
    main(slug_filter)
