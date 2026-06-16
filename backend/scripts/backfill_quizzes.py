"""Backfill quiz questions for topics that have content but no quiz yet.

Topics generated before the quiz tables existed have no questions. This walks
topics that have clips + sections and runs generate_and_store_questions for each
(idempotent: it skips topics that already have questions). Resume-safe.

Usage (from backend/):
    python -m scripts.backfill_quizzes                 # all eligible topics
    python -m scripts.backfill_quizzes 5               # first 5 only
    python -m scripts.backfill_quizzes a-slug b-slug   # specific slugs
"""
import sys
import logging

from dotenv import load_dotenv

load_dotenv()

from app.db.supabase import get_client
from app.services.quiz import generate_and_store_questions

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_quizzes")


def _eligible_slugs() -> list[tuple[str, str]]:
    """(slug, name) for topics that have both sections and clips."""
    db = get_client()
    sec = {r["topic_slug"] for r in db.table("topic_sections").select("topic_slug").limit(5000).execute().data}
    clip = {r["topic_slug"] for r in db.table("clips").select("topic_slug").limit(5000).execute().data}
    eligible = sorted(sec & clip)
    names = {r["slug"]: r.get("name") or r["slug"]
             for r in db.table("topics").select("slug,name").limit(5000).execute().data}
    return [(s, names.get(s, s.replace("-", " ").title())) for s in eligible]


def main() -> None:
    args = sys.argv[1:]
    if args and not args[0].isdigit():
        db = get_client()
        names = {r["slug"]: r.get("name") or r["slug"]
                 for r in db.table("topics").select("slug,name").limit(5000).execute().data}
        targets = [(s, names.get(s, s.replace("-", " ").title())) for s in args]
    else:
        targets = _eligible_slugs()
        if args:
            targets = targets[: int(args[0])]

    logger.info(f"Backfilling quizzes for {len(targets)} topic(s) (existing ones are skipped).")
    made = 0
    for slug, name in targets:
        try:
            n = generate_and_store_questions(slug, name)
            if n:
                made += 1
                logger.info(f"  {slug}: +{n}")
        except Exception as exc:
            logger.warning(f"  {slug}: failed ({exc})")
    logger.info(f"Done. Generated questions for {made}/{len(targets)} topic(s).")


if __name__ == "__main__":
    main()
