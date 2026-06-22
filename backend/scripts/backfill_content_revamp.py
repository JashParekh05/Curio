"""One-shot backfill for the content-retrieval-revamp tables.

After ``scripts/migration_content_revamp.sql`` has been applied, the new tables
(``canonical_arc``, ``curriculum_spine_nodes``, ``curriculum_spine_edges``,
``topics.arc_complete``) exist but are EMPTY. This operator-run script walks the
existing Topics through the already-shipped pure cores / shells to populate them,
so the revamp has data to work with immediately instead of waiting for organic
ingestion to refill it.

It is SAFE and additive-only:
  - it writes only to the NEW tables/columns introduced by the additive migration
    (``canonical_arc``, ``curriculum_spine_*``, ``topics.arc_complete``);
  - it never drops, deletes, or rewrites any existing Topic/Clip row;
  - the only ``delete`` it triggers is inside ``unify_and_store``, which clears a
    Topic's OWN prior ``canonical_arc`` rows before rewriting them (idempotent).

Re-running is safe: arc unification replaces a Topic's canonical_arc rows in place,
arc_complete is recomputed, and spine node/edge registration upserts (and is
deduped + cycle-checked by the pure spine core).

Passes:
  1. Arc unify   -- read each Topic's Role_Arc_Model / Legacy_Section_Model and
                    persist its Canonical_Arc (``arc_unifier_store.unify_and_store``).
  2. Arc_Complete-- for each Topic with a Canonical_Arc, mark ``topics.arc_complete``
                    true iff every arc role ordinal has at least one Clip.
  3. Spine nodes -- register every Arc_Complete, non-archived Topic as a Spine_Node.
  4. Spine edges -- register prerequisite edges from ``topics.prerequisites`` between
                    Topics that are both Spine_Nodes (cycle-rejected by the core).

Usage (from backend/):
  .venv/bin/python -m scripts.backfill_content_revamp            # run all passes
  .venv/bin/python -m scripts.backfill_content_revamp --dry-run  # report only, no writes
  .venv/bin/python -m scripts.backfill_content_revamp --limit 25 # first 25 topics only

ASCII only.
"""
from __future__ import annotations

import sys

from dotenv import load_dotenv

load_dotenv()

from app.db.supabase import get_client
from app.models.schemas import ArcRole
from app.services.arc_unifier import unify_arc
from app.services.arc_unifier_store import unify_and_store
from app.services.curriculum_spine import SpineEdge, SpineNode
from app.services.curriculum_spine_store import (
    register_spine_edge,
    register_spine_node,
)

# Map a Topic's recorded difficulty to a Curriculum_Spine Content_Level band.
_VALID_LEVELS = ("beginner", "intermediate", "advanced")
_DEFAULT_LEVEL = "intermediate"

# Per-Topic duration estimate (whole minutes) used when no Clip durations are
# available to sum: assume each short-form Clip is roughly this many minutes.
_ASSUMED_CLIP_MINUTES = 0.75

# Floor when a Topic has neither summable durations nor any counted Clips.
_DEFAULT_EST_MINUTES = 3


# ---------------------------------------------------------------------------
# Paged reads (Supabase caps a single select at 1000 rows; page explicitly)
# ---------------------------------------------------------------------------

def _read_all(db, table: str, columns: str, page: int = 1000) -> list[dict]:
    """Read every row of a table, paging in ``page``-sized ranges."""
    rows: list[dict] = []
    offset = 0
    while True:
        res = (
            db.table(table)
            .select(columns)
            .range(offset, offset + page - 1)
            .execute()
        )
        batch = list(res.data or [])
        rows.extend(batch)
        if len(batch) < page:
            break
        offset += page
    return rows


def _content_level(difficulty) -> str:
    """Best-effort map a Topic's difficulty value to a Content_Level band."""
    if isinstance(difficulty, str):
        d = difficulty.strip().lower()
        if d in _VALID_LEVELS:
            return d
        # common synonyms
        if d in ("easy", "intro", "basic", "novice"):
            return "beginner"
        if d in ("hard", "expert", "pro"):
            return "advanced"
    if isinstance(difficulty, (int, float)):
        if difficulty <= 1:
            return "beginner"
        if difficulty >= 3:
            return "advanced"
        return "intermediate"
    return _DEFAULT_LEVEL


def _parse_prereqs(value) -> list[str]:
    """Normalize a topics.prerequisites value into a list of slugs."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    if isinstance(value, str):
        # tolerate comma- or space-separated text
        parts = [p.strip() for p in value.replace(",", " ").split()]
        return [p for p in parts if p]
    return []


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(dry_run: bool, limit: int | None) -> None:
    db = get_client()

    topics = _read_all(db, "topics", "slug, name, difficulty, prerequisites, archived")
    topics = [t for t in topics if t.get("slug")]
    topics.sort(key=lambda t: t["slug"])
    if limit is not None:
        topics = topics[:limit]

    archived = {t["slug"] for t in topics if bool(t.get("archived"))}
    level_of = {t["slug"]: _content_level(t.get("difficulty")) for t in topics}
    prereqs_of = {t["slug"]: _parse_prereqs(t.get("prerequisites")) for t in topics}

    # Per-Topic clip role-ordinal counts, total counts, and duration sums (paged).
    clip_rows = _read_all(db, "clips", "topic_slug, role_ordinal, duration_seconds")
    role_counts: dict[str, dict[int, int]] = {}
    duration_secs: dict[str, float] = {}
    clip_count: dict[str, int] = {}
    for row in clip_rows:
        slug = row.get("topic_slug")
        if not slug:
            continue
        clip_count[slug] = clip_count.get(slug, 0) + 1
        dur = row.get("duration_seconds")
        if isinstance(dur, (int, float)):
            duration_secs[slug] = duration_secs.get(slug, 0.0) + float(dur)
        ordinal = row.get("role_ordinal")
        if ordinal is not None:
            counts = role_counts.setdefault(slug, {})
            counts[int(ordinal)] = counts.get(int(ordinal), 0) + 1

    def _est_minutes(slug: str) -> int:
        # Prefer the real summed clip duration when it is meaningfully present.
        secs = duration_secs.get(slug, 0.0)
        if secs >= 30:
            return max(1, round(secs / 60.0))
        # Otherwise estimate from clip count (durations missing/sub-30s total),
        # so a multi-clip topic does not collapse to a 1-minute estimate.
        n = clip_count.get(slug, 0)
        if n > 0:
            return max(1, round(n * _ASSUMED_CLIP_MINUTES))
        return _DEFAULT_EST_MINUTES

    # Old structure models, paged, grouped per Topic -- the inputs the pure
    # Arc_Unifier consumes. Read here so a --dry-run can simulate unification
    # without writing anything.
    role_model_of: dict[str, list[ArcRole]] = {}
    for row in _read_all(db, "topic_arc_roles", "topic_slug, role, ordinal"):
        slug = row.get("topic_slug")
        if not slug or row.get("role") is None or row.get("ordinal") is None:
            continue
        role_model_of.setdefault(slug, []).append(
            ArcRole(role=row["role"], ordinal=int(row["ordinal"]))
        )
    sections_of: dict[str, list[int]] = {}
    for row in _read_all(db, "topic_sections", "topic_slug, section_index"):
        slug = row.get("topic_slug")
        idx = row.get("section_index")
        if not slug or idx is None:
            continue
        sections_of.setdefault(slug, []).append(int(idx))

    print(f"\n{'='*72}\n content-revamp backfill  (topics={len(topics)}, "
          f"dry_run={dry_run})\n{'='*72}")

    # --- Pass 1: arc unify -------------------------------------------------
    produced = structureless = rejected = 0
    arcs: dict[str, object] = {}
    for t in topics:
        slug = t["slug"]
        if dry_run:
            # Simulate unification with the PURE core -- no writes.
            result = unify_arc(
                slug, role_model_of.get(slug, []), sections_of.get(slug, [])
            )
        else:
            result = unify_and_store(slug, db)
        if result.arc is not None:
            produced += 1
            arcs[slug] = result.arc
        elif getattr(result, "rejection", None) is not None:
            rejected += 1
        else:
            structureless += 1
    print(f"[1/4] arc unify     -> produced={produced} "
          f"structureless={structureless} rejected={rejected}")

    # --- Pass 2: arc_complete ---------------------------------------------
    complete = incomplete = 0
    complete_slugs: list[str] = []
    for slug, arc in arcs.items():
        counts = role_counts.get(slug, {})
        ordinals = [r.ordinal for r in arc.roles]
        is_complete = bool(ordinals) and all(counts.get(o, 0) >= 1 for o in ordinals)
        if is_complete:
            complete += 1
            complete_slugs.append(slug)
        else:
            incomplete += 1
        if not dry_run:
            try:
                db.table("topics").update({"arc_complete": is_complete}).eq(
                    "slug", slug
                ).execute()
            except Exception as exc:  # best-effort; column added by the migration
                print(f"    ! arc_complete update failed for {slug}: {exc}")
    print(f"[2/4] arc_complete  -> complete={complete} incomplete={incomplete}")

    # --- Pass 3: spine nodes ----------------------------------------------
    nodes_registered = 0
    spine_slugs: set[str] = set()
    for slug in complete_slugs:
        if slug in archived:
            continue
        node = SpineNode(
            topic_slug=slug,
            content_level=level_of.get(slug, _DEFAULT_LEVEL),
            est_minutes=_est_minutes(slug),
        )
        if dry_run:
            nodes_registered += 1
            spine_slugs.add(slug)
            continue
        if register_spine_node(node, db):
            nodes_registered += 1
            spine_slugs.add(slug)
    print(f"[3/4] spine nodes   -> registered={nodes_registered} "
          f"(arc_complete, non-archived)")

    # --- Pass 4: spine edges ----------------------------------------------
    edges_registered = edges_rejected = 0
    for slug in spine_slugs:
        for prereq in prereqs_of.get(slug, []):
            if prereq not in spine_slugs or prereq == slug:
                continue  # only wire edges between two spine nodes
            edge = SpineEdge(prerequisite=prereq, dependent=slug)
            if dry_run:
                edges_registered += 1
                continue
            result = register_spine_edge(edge, db)
            if getattr(result, "rejected_edge", None) is not None:
                edges_rejected += 1
            else:
                edges_registered += 1
    print(f"[4/4] spine edges   -> registered={edges_registered} "
          f"rejected_as_cycle={edges_rejected}")

    print(f"\n{'='*72}")
    if dry_run:
        print(" DRY RUN -- no rows written. Re-run without --dry-run to apply.")
    else:
        print(" Backfill complete. canonical_arc, topics.arc_complete, and the")
        print(" curriculum_spine_* tables are now populated.")
    print(f"{'='*72}\n")


if __name__ == "__main__":
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    limit = None
    if "--limit" in args:
        i = args.index("--limit")
        if i + 1 < len(args):
            limit = int(args[i + 1])
    main(dry_run, limit)
