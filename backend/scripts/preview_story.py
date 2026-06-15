"""Preview the story-quality pass on REAL clips, end to end, without writing any
clips to the database.

It composes the live stack: plan sections -> search + relevance-rank -> fetch
transcript -> segment into clips -> run the narrative director. Then it prints
the clips in arc order vs. the story-optimized order, with the judge's
per-dimension scores and notes, so you can eyeball whether the reordering tells
a better story.

Needs OPENAI_API_KEY, YOUTUBE_API_KEY, TRANSCRIPT_API_KEY (all in backend/.env).
The only DB writes are benign transcript-cache upserts. Run from backend/:

    python -m scripts.preview_story "Binary Search Trees"
    python -m scripts.preview_story "The French Revolution" beginner
"""
import logging
import sys

from dotenv import load_dotenv

load_dotenv()  # before app imports so module-level env reads see backend/.env

from app.agents.section_planner import _plan_sections
from app.agents.pipeline_agent import _rank_candidates
from app.services.pipeline import _identify_segments
from app.services.story import order_for_story
from app.services.youtube import _fetch_transcript
from scripts.preview_retrieval import _fetch_candidates


def _collect_clips(topic: str, difficulty: str) -> list[dict]:
    plan = _plan_sections(topic, difficulty)
    clips: list[dict] = []
    for section in plan:
        sec_idx = section["section_index"]
        query = section["search_query"]
        candidates = _rank_candidates(_fetch_candidates(query), query)
        for v in candidates:
            transcript = _fetch_transcript(v["video_id"])
            if not transcript:
                continue
            for seg in _identify_segments(transcript, topic):
                clips.append({
                    "section_index": sec_idx,
                    "title": seg.get("title", ""),
                    "description": seg.get("description", ""),
                    "transcript": seg.get("transcript", ""),
                })
            break  # one video per section keeps the preview fast
    return clips


def _show(label: str, clips: list[dict]) -> None:
    print(f"\n--- {label} ---")
    for i, c in enumerate(clips):
        print(f"  {i}. [§{c.get('section_index')}] {c.get('title')}")


def main() -> None:
    if len(sys.argv) < 2:
        print('Usage: python -m scripts.preview_story "<topic>" [difficulty]')
        raise SystemExit(1)
    topic = sys.argv[1]
    difficulty = sys.argv[2] if len(sys.argv) > 2 else "intermediate"
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print(f"\n=== Story preview for {topic!r} (difficulty={difficulty}) ===")
    clips = _collect_clips(topic, difficulty)
    if len(clips) < 2:
        print(f"\nOnly {len(clips)} clip(s) collected — not enough to judge a sequence.")
        return

    _show(f"ARC ORDER ({len(clips)} clips, as delivered today)", clips)

    ordered, verdict = order_for_story(topic, clips)

    if verdict:
        print("\n--- JUDGE VERDICT ---")
        print(f"overall_score: {verdict.get('overall_score')}")
        for dim, val in (verdict.get("dimensions") or {}).items():
            print(f"  {dim:<11} {val}")
        issues = [(c.get("index"), c.get("issue")) for c in verdict.get("clips", []) if c.get("issue")]
        if issues:
            print("  notes:")
            for idx, issue in issues:
                print(f"    [{idx}] {issue}")

    _show("STORY-OPTIMIZED ORDER", ordered)


if __name__ == "__main__":
    main()
