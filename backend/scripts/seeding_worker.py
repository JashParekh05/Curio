"""Seeding_Worker entrypoint: paced, resumable backlog drain (thin I/O shell).

The Seeding_Worker is the background/cron job that grows the cold-start library
without ever exceeding YouTube quota. It builds on ``scripts/seed_library.py``
(which seeds a fixed list) by instead draining the persisted Topic_Frontier one
Backlog_Item at a time, spending only what the Key_Pool reports affordable and
stopping cleanly at a per-run cap.

This module is a *thin I/O orchestration shell*: every decision delegates to the
pure cores and every read/write delegates to the I/O shells -- mirroring the
"pure decision core, thin I/O shell" convention used across the codebase. It owns
no policy of its own:

  - pacing / skip decisions -> ``app.services.seeding``
    (``can_process_next``, ``estimate_item_cost``, ``should_skip``)
  - quota accounting / selection -> ``app.services.quota_pool``
    (``remaining``, ``select_project``, ``charge``)
  - frontier selection / outcome -> ``app.services.topic_frontier``
    (``select_next``, ``apply_seed_outcome``)
  - quota persistence -> ``app.services.quota_store`` (``load_today``)
  - backlog persistence -> ``app.services.backlog_store``
    (``load_pending``, ``persist_status``, ``spawn_adjacent_for``)
  - generation -> the existing content-coherence pipeline
    (``topics._process_single_topic`` -> ``run_pipeline`` -> coherence/alignment
    passes), the SAME path learner-initiated topics use.

``run_once`` performs a single paced pass and returns a
``{processed, skipped, charged_units, stopped_reason}`` summary. It NEVER raises:
a per-item failure is logged with the topic id and the failure reason and the
loop continues, so a single bad topic can neither halt the run nor block any feed
request (the worker runs entirely out-of-band).

Ingestion routing (task 12.2): ``_generate`` drives each Backlog_Item through the
existing Ingestion_Pipeline (``topics._process_single_topic``) rather than a bare
``run_pipeline`` call, so seeded clips are plan-mapped (``plan_and_store_arc`` ->
``segment_into_atoms`` -> ``arc_assembler.assemble``) and coherence-checked
(``coherence.run_repair_loop`` + ``alignment.check_and_repair``) and persist with
``pedagogical_role``, ``role_ordinal``, covered concept, and coherence metadata
identical to learner-initiated clips. ``_generate`` additionally stamps the
topic's ``content_level`` (from the Backlog_Item's level) onto the persisted clips
so Discover level filtering works. The per-topic model-call budget and the
bounded coherence/alignment repair loops are owned by that existing machinery and
are reused, not rebuilt here; the YouTube Quota_Cost stays paced by the Key_Pool
through the run loop and the single ``youtube.youtube_search`` charge site.

ASCII only.

Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12,
6.1, 6.7, 6.8, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9, 8.10, 8.11, 8.12
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from app.db.supabase import get_client
from app.services import backlog_store, quota_store
from app.services.quota_pool import ProjectQuota, charge, remaining, select_project
from app.services.seeding import (
    DEFAULT_PER_RUN_CAP,
    can_process_next,
    estimate_item_cost,
    should_skip,
)
from app.services.topic_frontier import BacklogItem, apply_seed_outcome, select_next

logger = logging.getLogger(__name__)

#: Per-item Quota_Cost estimate used by the pacing gate. The deep-ingestion
#: routing in task 12.2 will derive a per-topic section/cached-query count; until
#: then a Backlog_Item is paced as one not-yet-cached search query (one
#: ``youtube/v3/search`` + one ``videos.list``). Sourced from the pure estimator
#: so the single source of truth for Quota_Cost stays in ``seeding``/``quota_pool``.
_DEFAULT_SECTION_COUNT: int = 1
_DEFAULT_CACHED_QUERIES: int = 0

# Stop reasons reported in the run summary.
_STOP_EMPTY = "backlog_empty"          # no pending Backlog_Item remained (Req 2.10)
_STOP_CAP = "per_run_cap"              # per-run processing cap reached (Req 2.12)
_STOP_BUDGET = "budget_exhausted"      # first unaffordable item hit (Req 2.2, 2.11)


def _topic_has_clips(topic: str) -> bool:
    """Best-effort check: does ``topic`` already have at least one stored clip?

    Mirrors the skip check in ``discover_seeding._seed_topics_bg``: a topic that
    already has clips must be skipped with zero quota (Req 2.4, 6.6). Best-effort
    -- any read failure degrades to ``False`` so generation is attempted rather
    than silently dropped; the pipeline itself remains idempotent.
    """
    try:
        db = get_client()
        res = (
            db.table("clips")
            .select("id")
            .eq("topic_slug", topic)
            .limit(1)
            .execute()
        )
        return bool(res.data)
    except Exception as exc:
        logger.warning("[worker] has-clips check failed for '%s': %s", topic, exc)
        return False


def _count_clips(topic: str) -> int:
    """Best-effort count of clips currently stored for ``topic``.

    Used to decide success of an ingestion run (``>= 1`` means the topic now has
    library content). Best-effort: any read failure degrades to 0 so a transient
    DB error is treated as "no clips" (the Backlog_Item is left pending for a
    future retry) rather than a false success.
    """
    try:
        db = get_client()
        res = (
            db.table("clips")
            .select("id")
            .eq("topic_slug", topic)
            .execute()
        )
        return len(res.data or [])
    except Exception as exc:
        logger.warning("[worker] clip count failed for '%s': %s", topic, exc)
        return 0


def _stamp_content_level(topic: str, level: str | None) -> None:
    """Stamp the topic's Content_Level onto every persisted clip for ``topic``.

    The Ingestion_Pipeline (``_process_single_topic``) stores clips with their
    coherence metadata but does not set ``content_level``; we denormalize the
    Backlog_Item's level onto the clips here so Discover level filtering
    (``level_filter``) can rank by exact level without joining ``topics`` on every
    read (Req 8.5, and the cold-start Discover serving path). Idempotent and
    best-effort: a missing level is a no-op and any write failure is logged and
    swallowed so it can never stall the worker.
    """
    if not level:
        return
    try:
        db = get_client()
        (
            db.table("clips")
            .update({"content_level": level})
            .eq("topic_slug", topic)
            .execute()
        )
        logger.info("[worker] stamped content_level='%s' on clips for '%s'", level, topic)
    except Exception as exc:
        logger.warning("[worker] content_level stamp failed for '%s': %s", topic, exc)


def _generate(item: BacklogItem) -> int:
    """Ingest a Backlog_Item through the existing content-coherence pipeline.

    Routes the topic through ``topics._process_single_topic`` -- the SAME path
    learner-initiated topics use -- so the seeded clips are plan-mapped and
    coherence-checked rather than raw segmentation output (Req 2.3, 8.1-8.6):

      plan_and_store_sections -> run_pipeline per section (fetch/decode transcript
      -> segment_into_atoms) -> plan_and_store_arc -> arc_assembler.assemble ->
      coherence.run_repair_loop -> alignment.check_and_repair -> persist clips
      with pedagogical_role, role_ordinal, covered concept and coherence metadata
      -> story/quiz passes.

    That machinery is best-effort end to end: a video whose transcript cannot be
    fetched/decoded is skipped, a topic that yields no atoms produces no
    library-eligible clips, and only the coherence/alignment-annotated subset is
    promoted -- none of which raises (Req 8.7, 8.11, 8.12). The per-topic
    model-call budget and the bounded repair loops live inside that existing
    machinery and are reused, not rebuilt here (Req 8.8).

    After ingestion, the topic's Content_Level (from ``item.level``) is stamped
    onto the persisted clips so Discover level filtering works (Req 8.5).

    Idempotency / resume (Req 8.9, 8.10, 2.4, 2.7, 6.6): a topic that already has
    clips is left untouched and consumes zero model calls; its existing clip count
    is returned so the run loop marks it done. The run loop also gates this via
    ``should_skip`` before calling ``_generate``, so this guard is a defensive
    second line for direct calls.

    Returns the number of clips stored for the topic (``>= 1`` means the seed
    succeeded). Never raises: any failure degrades to a clip count (0 leaves the
    Backlog_Item pending for retry).

    Validates: Requirements 2.3, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9,
    8.10, 8.11, 8.12
    """
    slug = item.topic
    name = slug.replace("-", " ").title()

    # Idempotent skip: an already-seeded topic is left unchanged and ingested with
    # zero model calls; report its existing clip count so the outcome is "done".
    if _topic_has_clips(slug):
        existing = _count_clips(slug)
        logger.info(
            "[worker] '%s' already seeded (%d clip(s)); skipping ingestion (0 model calls)",
            slug, existing,
        )
        _stamp_content_level(slug, item.level)
        return existing

    # Best-effort topic upsert so the pipeline reads the item's Content_Level as
    # the topic difficulty (drives section planning and the Planned_Arc).
    try:
        db = get_client()
        existing = db.table("topics").select("slug").eq("slug", slug).execute()
        if not existing.data:
            db.table("topics").insert({
                "slug": slug,
                "name": name,
                "difficulty": item.level,
                "prerequisites": [],
            }).execute()
    except Exception as exc:
        logger.warning("[worker] topic upsert failed for '%s': %s", slug, exc)

    # Route through the existing Ingestion_Pipeline (the learner-initiated path).
    # Imported lazily to avoid an import-time cycle (topics -> services -> ...).
    from app.api.topics import _process_single_topic

    try:
        asyncio.run(_process_single_topic(slug, name))
    except Exception as exc:
        # _process_single_topic is itself best-effort and should not raise, but
        # guard the event-loop boundary so a stray error never halts the run.
        logger.warning("[worker] ingestion pipeline failed for '%s': %s", slug, exc)

    # Stamp the Content_Level onto whatever coherent clips were persisted.
    _stamp_content_level(slug, item.level)

    return _count_clips(slug)


def run_once(per_run_cap: int = DEFAULT_PER_RUN_CAP,
             now_utc: datetime | None = None) -> dict:
    """Perform one paced pass over the Topic_Frontier backlog.

    Loads today's per-project quota usage and the pending backlog, then loops:
    ``select_next`` -> per-run-cap gate -> ``can_process_next`` pacing gate ->
    ``should_skip`` -> ``select_project`` affordability -> generate ->
    ``apply_seed_outcome``/``persist_status``, spawning up to
    ``MAX_ADJACENT_PER_SEED`` adjacent topics on success. The run stops cleanly on
    an empty backlog, the per-run cap, or the first unaffordable Backlog_Item, and
    never overspends the affordable budget (the selected project's in-memory quota
    is charged the per-item estimate each success so the cumulative pace is
    bounded; the real spend is persisted inside the youtube charge site).

    NEVER raises: a per-item generation failure is logged with the topic id and
    the failure reason, the Backlog_Item is left ``pending`` for retry, and the
    loop continues with the next item (Req 2.8, 6.8). The worker is a background
    entrypoint and never blocks any feed request (Req 2.9, 6.1).

    Args:
        per_run_cap: Maximum Backlog_Items to process in this run; defaults to
            ``DEFAULT_PER_RUN_CAP`` when unset (Req 2.12).
        now_utc: Optional clock injection for the quota window; defaults to now.

    Returns:
        A summary dict ``{processed, skipped, charged_units, stopped_reason}``.

    Validates: Requirements 2.1, 2.2, 2.4, 2.7, 2.8, 2.9, 2.10, 2.11, 2.12,
    6.1, 6.7, 6.8
    """
    processed = 0
    skipped = 0
    charged_units = 0
    stopped_reason = _STOP_EMPTY

    # I/O reads up front; both fail closed/degrade to empty (no spend) on error.
    projects: list[ProjectQuota] = quota_store.load_today(now_utc)
    working: list[BacklogItem] = backlog_store.load_pending()

    while True:
        item = select_next(working)
        if item is None:
            stopped_reason = _STOP_EMPTY
            logger.info("[worker] backlog empty; ending run cleanly")
            break

        # Per-run cap gate (Req 2.12). Checked before pacing so a reached cap is
        # reported distinctly from an exhausted budget.
        if processed >= per_run_cap:
            stopped_reason = _STOP_CAP
            logger.info("[worker] per-run cap of %d reached; stopping run", per_run_cap)
            break

        est_cost = estimate_item_cost(_DEFAULT_SECTION_COUNT, _DEFAULT_CACHED_QUERIES)
        remaining_affordable = max((remaining(p) for p in projects), default=0)

        # Pacing gate: stop at the first item we cannot afford (Req 2.1, 2.2, 2.11).
        if not can_process_next(remaining_affordable, est_cost, processed, per_run_cap):
            stopped_reason = _STOP_BUDGET
            logger.info(
                "[worker] next item '%s' (est %d units) unaffordable; stopping run",
                item.topic, est_cost,
            )
            break

        # Commit to this item: remove it from the working set so it is never
        # reselected this run, regardless of outcome (success marks done in the
        # store; failure leaves it pending for a future run).
        working = [it for it in working if it.topic != item.topic]

        try:
            # Skip already-populated/done topics with zero quota (Req 2.4, 6.6).
            if should_skip(_topic_has_clips(item.topic), item.status):
                backlog_store.persist_status(item.topic, "done")
                skipped += 1
                logger.info("[worker] skipped '%s' (already populated/done)", item.topic)
                continue

            # Affordability at project granularity (no cost splitting). Should
            # always succeed given the pacing gate above; guard defensively.
            project_id = select_project(projects, est_cost)
            if project_id is None:
                stopped_reason = _STOP_BUDGET
                logger.info(
                    "[worker] no project can afford '%s' (est %d units); stopping run",
                    item.topic, est_cost,
                )
                break

            stored = _generate(item)
            success = stored >= 1

            # Record outcome via the pure core (success -> done; failure -> retry).
            outcome = apply_seed_outcome([item], item.topic, success)
            if success:
                # Pace the cumulative spend by charging the selected project's
                # in-memory quota the per-item estimate; bounds the run so it can
                # never overspend the affordable budget (Req 2.1, 2.11).
                projects = [
                    charge(p, est_cost) if p.project_id == project_id else p
                    for p in projects
                ]
                charged_units += est_cost
                # Persist the done transition produced by apply_seed_outcome.
                backlog_store.persist_status(outcome[0].topic, outcome[0].status)
                processed += 1
                logger.info(
                    "[worker] seeded '%s' (%d clip(s), est %d units charged)",
                    item.topic, stored, est_cost,
                )
                # Grow the frontier from the freshly seeded topic (Req 2.x / 3.3).
                backlog_store.spawn_adjacent_for(item.topic)
            else:
                # Failure: leave status unchanged for retry, continue (Req 2.8).
                logger.warning(
                    "[worker] generation for '%s' produced no clips; left pending for retry",
                    item.topic,
                )
        except Exception as exc:
            # Per-item failure must never halt the run (Req 2.8, 6.8).
            logger.warning(
                "[worker] generation for '%s' failed: %s; left pending for retry",
                item.topic, exc,
            )
            continue

    summary = {
        "processed": processed,
        "skipped": skipped,
        "charged_units": charged_units,
        "stopped_reason": stopped_reason,
    }
    logger.info("[worker] run complete: %s", summary)
    return summary


def main() -> None:
    """CLI entrypoint: run one paced pass, with an optional per-run cap argument.

    Usage (from backend/):
        python -m scripts.seeding_worker            # default per-run cap
        python -m scripts.seeding_worker 10         # process at most 10 items
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    args = sys.argv[1:]
    cap = DEFAULT_PER_RUN_CAP
    if args and args[0].isdigit():
        cap = int(args[0])
    run_once(per_run_cap=cap)


if __name__ == "__main__":
    main()
