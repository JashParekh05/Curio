"""Read-only database audit: find the mess before cleaning it.

Inspects the live Supabase schema and reports, WITHOUT writing anything:

  1. Row counts for every known table (and which tables are missing).
  2. Orphans: child rows whose topic_slug / clip_id / question_id has no parent.
  3. clips column health: null-rate per column, to expose the legacy-vs-arc
     split (section_index vs role_ordinal) and likely-dead scoring columns.
  4. Duplicate transcript storage across clips / learning_atoms / transcript_cache.
  5. The dual arc model: topics with topic_sections but no topic_arcs (and vice
     versa) — the structural debt to unify.
  6. Whether the alternative-content-streams migration has been applied.

Nothing is written; this is purely diagnostic.

Usage:
    cd backend
    python -m scripts.audit_db            # full report
    python -m scripts.audit_db 20         # show up to 20 sample ids per finding
"""
import sys

from dotenv import load_dotenv

load_dotenv()

from app.db.supabase import get_client

SAMPLE = 10  # default number of example ids printed per finding

# Tables we expect, grouped only for readable output.
KNOWN_TABLES = [
    "topics", "clips", "topic_sections", "topic_arcs", "topic_arc_roles",
    "learning_atoms", "segment_verdicts", "coherence_defects",
    "ingestion_jobs", "ingestion_funnel", "transcript_cache",
    "youtube_search_cache", "project_quota_usage", "topic_backlog",
    "clip_events", "impressions", "quiz_questions", "quiz_results",
    "learning_paths", "user_profiles", "session_embeddings",
]

# child table -> (its key column, parent table, parent key column)
SLUG_CHILDREN = [
    ("clips", "topic_slug"),
    ("topic_sections", "topic_slug"),
    ("topic_arcs", "topic_slug"),
    ("topic_arc_roles", "topic_slug"),
    ("learning_atoms", "topic_slug"),
    ("segment_verdicts", "topic_slug"),
    ("coherence_defects", "topic_slug"),
    ("ingestion_jobs", "topic_slug"),
    ("ingestion_funnel", "topic_slug"),
    ("quiz_questions", "topic_slug"),
    ("quiz_results", "topic_slug"),
    ("impressions", "topic_slug"),
]

# clips columns to inspect for null-rate (skip the big embedding payload).
CLIP_COLUMNS = [
    "topic_slug", "title", "video_url", "transcript", "source_platform",
    "hook_score", "section_index", "story_score", "narrative_rank",
    "pedagogical_role", "role_ordinal", "concept_label", "engagement_score",
]


def _db():
    return get_client()


def count_rows(table: str) -> int | None:
    """Exact row count via a head-style count query, or None if the table is absent."""
    try:
        res = _db().table(table).select("*", count="exact").limit(1).execute()
        return res.count
    except Exception:
        return None


def null_count(table: str, col: str) -> int | None:
    """Count rows where col IS NULL, or None if the column/table is absent."""
    try:
        res = _db().table(table).select(col, count="exact").is_(col, "null").limit(1).execute()
        return res.count
    except Exception:
        return None


def fetch_column(table: str, col: str, cap: int = 200_000) -> list | None:
    """Page through a single column's values. None if table/column is absent."""
    out: list = []
    start, page = 0, 1000
    try:
        while True:
            res = _db().table(table).select(col).range(start, start + page - 1).execute()
            rows = res.data or []
            out.extend(r.get(col) for r in rows)
            if len(rows) < page or len(out) >= cap:
                break
            start += page
        return out
    except Exception:
        return None


def _pct(n: int, d: int) -> str:
    return "  n/a" if not d else f"{100 * n / d:5.1f}%"


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def report_row_counts() -> dict[str, int]:
    print(f"\n{'='*70}\n TABLE ROW COUNTS\n{'='*70}")
    counts: dict[str, int] = {}
    missing: list[str] = []
    for t in KNOWN_TABLES:
        c = count_rows(t)
        if c is None:
            missing.append(t)
            print(f"  {'MISSING':>8}  {t}")
        else:
            counts[t] = c
            print(f"  {c:>8}  {t}")
    if missing:
        print(f"\n  {len(missing)} expected table(s) not found: {', '.join(missing)}")
    return counts


def report_orphans(sample: int) -> None:
    print(f"\n{'='*70}\n ORPHANS — child rows whose parent key does not exist\n{'='*70}")

    valid_slugs = set(s for s in (fetch_column("topics", "slug") or []) if s)
    print(f"  topics: {len(valid_slugs)} valid slugs\n")

    print("  -- by topic_slug (parent: topics.slug) --")
    for table, col in SLUG_CHILDREN:
        vals = fetch_column(table, col)
        if vals is None:
            print(f"  {table:<22} {col:<14} (table/column absent)")
            continue
        present = [v for v in vals if v is not None]
        orphans = [v for v in present if v not in valid_slugs]
        uniq = sorted(set(orphans))
        flag = "  <-- ORPHANS" if orphans else ""
        print(f"  {table:<22} {len(present):>6} rows, {len(orphans):>5} orphaned "
              f"({_pct(len(orphans), len(present))}){flag}")
        if uniq:
            print(f"  {'':<22} missing slugs: {', '.join(map(str, uniq[:sample]))}"
                  f"{' ...' if len(uniq) > sample else ''}")

    # id-based parents
    print("\n  -- by foreign id --")
    _orphan_by_id("clip_events", "clip_id", "clips", "id", sample)
    _orphan_by_id("impressions", "clip_id", "clips", "id", sample)
    _orphan_by_id("quiz_results", "question_id", "quiz_questions", "id", sample)


def _orphan_by_id(child: str, col: str, parent: str, pcol: str, sample: int) -> None:
    parent_ids = fetch_column(parent, pcol)
    child_vals = fetch_column(child, col)
    if parent_ids is None or child_vals is None:
        print(f"  {child:<22} {col:<14} (table/column absent)")
        return
    valid = set(v for v in parent_ids if v is not None)
    present = [v for v in child_vals if v is not None]
    orphans = [v for v in present if v not in valid]
    flag = "  <-- ORPHANS" if orphans else ""
    print(f"  {child:<22} {len(present):>6} rows, {len(orphans):>5} orphaned "
          f"-> {parent}.{pcol} ({_pct(len(orphans), len(present))}){flag}")


def report_clip_columns() -> None:
    print(f"\n{'='*70}\n CLIPS COLUMN HEALTH — null-rate per column\n{'='*70}")
    total = count_rows("clips") or 0
    print(f"  clips total rows: {total}\n")
    print(f"  {'nulls':>7} {'null%':>7}  column")
    print("  " + "-" * 50)
    for col in CLIP_COLUMNS:
        n = null_count("clips", col)
        if n is None:
            print(f"  {'absent':>7}         {col}")
            continue
        note = ""
        if total and n == total:
            note = "  <-- ALWAYS NULL (dead?)"
        elif total and n > total * 0.9:
            note = "  <-- ~always null"
        print(f"  {n:>7} {_pct(n, total):>7}  {col}{note}")


def report_transcript_duplication() -> None:
    print(f"\n{'='*70}\n TRANSCRIPT STORAGE — same text in multiple tables\n{'='*70}")
    clips_total = count_rows("clips") or 0
    clips_null = null_count("clips", "transcript")
    clips_with = (clips_total - clips_null) if clips_null is not None else None
    atoms_total = count_rows("learning_atoms")
    tcache_total = count_rows("transcript_cache")
    print(f"  clips with transcript text : "
          f"{clips_with if clips_with is not None else 'n/a'} / {clips_total}")
    print(f"  learning_atoms rows        : {atoms_total}")
    print(f"  transcript_cache rows      : {tcache_total}")
    print("  (transcript text is stored in up to 3 places; cache is the intended home.)")


def report_dual_arc(sample: int) -> None:
    print(f"\n{'='*70}\n DUAL ARC MODEL — topic_sections vs topic_arcs\n{'='*70}")
    sec = set(s for s in (fetch_column("topic_sections", "topic_slug") or []) if s)
    arc = set(s for s in (fetch_column("topic_arcs", "topic_slug") or []) if s)
    topics = set(s for s in (fetch_column("topics", "slug") or []) if s)
    print(f"  topics with sections only : {len(sec - arc)}")
    print(f"  topics with arcs only     : {len(arc - sec)}")
    print(f"  topics with both          : {len(sec & arc)}")
    print(f"  topics with neither       : {len(topics - sec - arc)}")
    only_sec = sorted(sec - arc)
    if only_sec:
        print(f"  sections-only e.g.: {', '.join(only_sec[:sample])}"
              f"{' ...' if len(only_sec) > sample else ''}")


def report_migration_applied() -> None:
    print(f"\n{'='*70}\n ALTERNATIVE-CONTENT-STREAMS MIGRATION\n{'='*70}")
    checks = {
        "provider_registry table": count_rows("provider_registry") is not None,
        "provider_spend table": count_rows("provider_spend") is not None,
        "provider_search_cache table": count_rows("provider_search_cache") is not None,
        "clips.provider_id column": null_count("clips", "provider_id") is not None,
        "clips.external_id column": null_count("clips", "external_id") is not None,
        "clips.content_id column": null_count("clips", "content_id") is not None,
    }
    for name, ok in checks.items():
        print(f"  [{'x' if ok else ' '}] {name}")
    if all(checks.values()):
        print("\n  Migration appears APPLIED.")
    elif not any(checks.values()):
        print("\n  Migration NOT applied — run scripts/migration_alt_streams.sql in Supabase.")
    else:
        print("\n  Migration PARTIALLY applied — review scripts/migration_alt_streams.sql.")


def main(sample: int) -> None:
    try:
        _db()
    except Exception as exc:
        print(f"DB unreachable (check SUPABASE_URL / SUPABASE_KEY in .env): {exc}")
        return
    report_row_counts()
    report_orphans(sample)
    report_clip_columns()
    report_transcript_duplication()
    report_dual_arc(sample)
    report_migration_applied()
    print(f"\n{'='*70}\n Read-only audit complete. Nothing was written.\n{'='*70}\n")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a.isdigit()]
    main(int(args[0]) if args else SAMPLE)
