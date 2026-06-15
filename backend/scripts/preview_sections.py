"""Preview the decomposed section planner (outline -> detail -> judge -> revise)
for a topic WITHOUT touching the database. Use it to eyeball plan quality and
tune the LLM-as-judge rubric/thresholds.

Only needs OPENAI_API_KEY (no Supabase / YouTube). Run from the backend/ dir:

    OPENAI_API_KEY=sk-... python -m scripts.preview_sections "Binary Search Trees"
    OPENAI_API_KEY=sk-... python -m scripts.preview_sections "The French Revolution" beginner

The planner's per-round judge logs (score + weak sections + issues) print as it
runs; the final plan and a fresh judge verdict on it print at the end.
"""
import logging
import sys

from app.agents.section_planner import _plan_sections, _judge_sections


def main() -> None:
    if len(sys.argv) < 2:
        print('Usage: python -m scripts.preview_sections "<topic name>" [difficulty]')
        raise SystemExit(1)
    topic = sys.argv[1]
    difficulty = sys.argv[2] if len(sys.argv) > 2 else "intermediate"

    # Surface the planner's [section_planner] judge-round logs.
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print(f"\n=== Planning sections for {topic!r} (difficulty={difficulty}) ===\n")
    sections = _plan_sections(topic, difficulty)

    print("\n--- FINAL PLAN ---")
    for s in sections:
        print(f"\n[{s['section_index']}] {s['title']}")
        print(f"    desc:  {s['description']}")
        print(f"    query: {s['search_query']}")

    print("\n--- JUDGE VERDICT ON FINAL PLAN ---")
    try:
        verdict = _judge_sections(topic, difficulty, sections)
        print(f"overall_score: {verdict.get('overall_score')}")
        for s in verdict.get("sections", []):
            flag = "ok  " if s.get("ok") else "WEAK"
            print(f"  [{s.get('section_index')}] {flag} {s.get('issue') or ''}")
    except Exception as exc:
        print(f"(judge call failed: {exc})")


if __name__ == "__main__":
    main()
