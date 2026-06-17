"""Pure pacing core for the Seeding_Worker.

The Seeding_Worker (``scripts/seeding_worker.py``) drains the Topic_Frontier
backlog one item at a time, spending only what the Key_Pool reports affordable
and never exceeding a per-run cap. This module holds the *pure* pacing decisions
behind that loop — no DB reads, no clock reads, no global mutation — mirroring
``quota_pool.py``, ``self_heal_state.py``, and ``coherence_budget.py``. The thin
I/O shell that loads state, charges quota, and runs the pipeline lives in
``scripts/seeding_worker.py``; this module never touches it.

The functions answer the three questions the worker loop asks on each iteration:
may we process another Backlog_Item given remaining budget and the per-run cap
(``can_process_next``), how many Quota_Budget units would an item cost given that
cached queries are free (``estimate_item_cost``), and should generation be
skipped entirely because the topic already has clips or is done (``should_skip``).

Quota costs are sourced from ``quota_pool`` so there is a single source of truth:
a ``youtube/v3/search`` costs ``SEARCH_COST`` units and a ``videos.list``
metadata call costs ``METADATA_COST`` units. ASCII only.

Validates: Requirements 2.1, 2.2, 2.4, 2.5, 2.6, 2.11, 2.12, 6.6, 6.9
"""
from __future__ import annotations

from app.services.quota_pool import METADATA_COST, SEARCH_COST

# ---------------------------------------------------------------------------
# Policy constants
# ---------------------------------------------------------------------------

#: Maximum number of Backlog_Items a single worker run processes when no per-run
#: cap is configured (Req 2.12).
DEFAULT_PER_RUN_CAP: int = 25


# ---------------------------------------------------------------------------
# Pure pacing functions
# ---------------------------------------------------------------------------

def can_process_next(remaining_affordable: int, est_item_cost: int,
                     processed_count: int, per_run_cap: int) -> bool:
    """Return True iff the worker may process another Backlog_Item this run.

    True if and only if the per-run cap has not been reached
    (``processed_count < per_run_cap``), the next item's estimated Quota_Cost is
    affordable (``est_item_cost <= remaining_affordable``), and that cost is
    positive (``est_item_cost > 0``). This is the pacing gate that keeps a run
    from overspending the affordable budget and from exceeding the per-run cap;
    a non-positive estimate is rejected so a zero-cost item never advances the
    paced spend loop. Pure; reads no state (Req 2.1, 2.2, 2.11, 2.12).

    Validates: Requirements 2.1, 2.2, 2.11, 2.12
    """
    if est_item_cost <= 0:
        return False
    if processed_count >= per_run_cap:
        return False
    return est_item_cost <= remaining_affordable


def estimate_item_cost(section_count: int, cached_queries: int) -> int:
    """Return the estimated Quota_Cost to seed a Backlog_Item, never negative.

    Each not-yet-cached query for the topic costs one ``youtube/v3/search``
    (``SEARCH_COST``) plus one ``videos.list`` metadata call (``METADATA_COST``);
    cached queries reuse ``youtube_search_cache``/``transcript_cache`` and cost
    nothing. The number of paid queries is ``section_count - cached_queries``,
    clamped at 0 so a fully (or over-) cached topic estimates as free. Pure
    (Req 2.5, 2.6).

    Validates: Requirements 2.5, 2.6
    """
    paid_queries = max(0, section_count - cached_queries)
    return paid_queries * (SEARCH_COST + METADATA_COST)


def should_skip(has_clips: bool, status: str) -> bool:
    """Return True iff seeding this topic should be skipped, consuming no quota.

    Generation is skipped — charging zero Quota_Budget and regenerating nothing —
    when the topic already has at least one clip (``has_clips``) or its backlog
    status is ``done``. Pure; reads no state (Req 2.4, 6.6).

    Validates: Requirements 2.4, 6.6
    """
    return has_clips or status == "done"
