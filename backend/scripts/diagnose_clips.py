"""Clip-quality diagnostic: which clips are losing your viewers.

Reads clip_events telemetry and ranks individual clips worst-first by how well
they hold attention (avg watch ratio + early-skip rate), then rolls the same
data up per topic. Use it to drive the next seeding pass with what your real
users skipped instead of guessing.

Nothing is written; this is read-only.

Usage:
    cd backend
    python -m scripts.diagnose_clips                 # top 25 worst clips + topic rollup
    python -m scripts.diagnose_clips 50              # top 50 worst clips
    python -m scripts.diagnose_clips 50 --all        # include low-confidence (<min_views) clips
"""
import sys

from dotenv import load_dotenv

load_dotenv()

from app.services.analytics import worst_clips

MIN_VIEWS = 3


def _pct(x) -> str:
    return "  n/a" if x is None else f"{x * 100:5.1f}%"


def _trunc(s: str | None, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 3] + "..."


def main(limit: int, include_low_conf: bool) -> None:
    rows = worst_clips(min_views=MIN_VIEWS)
    if not rows:
        print("No clip_events data yet (or DB unreachable). Get some watches first.")
        return

    actionable = [r for r in rows if not r["low_confidence"] and r["avg_watch_ratio"] is not None]
    shown = rows if include_low_conf else actionable

    # want_more / already_know map to the in-app feedback signals.
    print(f"\n{'='*78}\n WORST CLIPS - fix/replace these first "
          f"(watch% asc; >= {MIN_VIEWS} views unless --all)\n{'='*78}")
    print(f"{'watch%':>7} {'skip%':>6} {'views':>5} {'more':>4} {'know':>4}  "
          f"{'topic':<22} {'beat':>4}  title")
    print("-" * 78)
    for r in shown[:limit]:
        beat = "-" if r["section_index"] is None else str(r["section_index"])
        flag = " (low-n)" if r["low_confidence"] else ""
        print(f"{_pct(r['avg_watch_ratio'])} {_pct(r['skip_rate'])} {r['views']:>5} "
              f"{r['want_more']:>4} {r['already_know']:>4}  "
              f"{_trunc(r['topic_slug'], 22):<22} {beat:>4}  {_trunc(r['title'], 30)}{flag}")

    # Per-topic rollup: view-weighted avg watch ratio, worst topics first.
    topics: dict[str, dict] = {}
    for r in actionable:
        t = topics.setdefault(r["topic_slug"], {"views": 0, "wsum": 0.0, "clips": 0})
        t["views"] += r["views"]
        t["wsum"] += r["avg_watch_ratio"] * r["views"]
        t["clips"] += 1
    rollup = sorted(
        ({"topic": k, "views": v["views"], "clips": v["clips"],
          "watch": v["wsum"] / v["views"] if v["views"] else None} for k, v in topics.items()),
        key=lambda x: (x["watch"] if x["watch"] is not None else 1.0),
    )

    print(f"\n{'='*78}\n WORST TOPICS - reseed these first (view-weighted watch%)\n{'='*78}")
    print(f"{'watch%':>7} {'views':>6} {'clips':>6}  topic")
    print("-" * 78)
    for t in rollup:
        print(f"{_pct(t['watch'])} {t['views']:>6} {t['clips']:>6}  {t['topic']}")

    print(f"\n{len(actionable)} clips with >= {MIN_VIEWS} views measured "
          f"({len(rows) - len(actionable)} below threshold, hidden unless --all).")
    print("Lowest watch% = your re-cut/replace list. Worst topics = your reseed list.\n")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--all"]
    include_low_conf = "--all" in sys.argv[1:]
    limit = int(args[0]) if args else 25
    main(limit, include_low_conf)
