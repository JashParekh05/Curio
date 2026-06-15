"""Pre-seed the Discover library so a brand-new user lands on instant content.

Generates clips for a curated set of broadly appealing, high-curiosity topics.
Idempotent and resume-safe: topics that already have clips are skipped (via
_seed_topics_bg), so re-running only fills gaps.

Usage (from backend/):
    python -m scripts.seed_library                      # full default library
    python -m scripts.seed_library 6                    # first 6 topics only
    python -m scripts.seed_library black-holes the-cold-war   # specific slugs
"""
import sys
import logging

from dotenv import load_dotenv

load_dotenv()

from app.services.discover_seeding import _seed_topics_bg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("seed_library")

# Broadly appealing across domains — the goal is a first scroll that feels
# instantly interesting regardless of who shows up. Slugs are searched by name
# by the pipeline, so they double as readable topic titles.
LIBRARY: list[str] = [
    "black-holes", "the-big-bang", "quantum-entanglement",
    "how-vaccines-work", "crispr-gene-editing", "dna-replication",
    "photosynthesis", "evolution-by-natural-selection", "the-human-brain",
    "neural-networks-basics", "how-the-internet-works", "how-gps-works",
    "the-roman-empire", "the-french-revolution", "the-cold-war",
    "compound-interest", "the-stock-market-explained", "inflation-explained",
    "game-theory-basics", "cognitive-biases", "supply-and-demand",
    "newtons-laws-of-motion", "climate-change-explained", "the-water-cycle",
]


def _select_slugs(args: list[str]) -> list[str]:
    """Resolve CLI args to the slugs to seed (pure, testable):
      - no args        -> the full library
      - a single int N -> the first N library topics
      - explicit slugs -> exactly those
    """
    if len(args) == 1 and args[0].isdigit():
        return LIBRARY[: int(args[0])]
    if args:
        return args
    return list(LIBRARY)


def main() -> None:
    slugs = _select_slugs(sys.argv[1:])
    logger.info(f"Seeding {len(slugs)} library topic(s) — existing clips are skipped.")
    _seed_topics_bg(slugs, difficulty="beginner")
    logger.info("Done.")


if __name__ == "__main__":
    main()
