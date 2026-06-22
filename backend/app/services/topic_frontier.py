"""Topic_Frontier / Backlog pure decision core.

The Topic_Frontier is a persisted, prioritized queue of topics awaiting
seeding.  It is seeded from the grade x interest matrix, regrows by spawning
adjacent topics on successful seeding and from learner engagement, is bounded
in size, deduped by topic slug, and prioritized by a pure deterministic
function.

This module holds ONLY the pure decision logic (no DB, no clock, no globals
read), mirroring the convention in ``self_heal_state.py`` and
``coherence_budget.py``: a pure decision core wrapped elsewhere by a thin
best-effort I/O shell (``backlog_store.py``).  Every function here is
deterministic in its inputs and free of side effects -- it never mutates the
``BacklogItem`` instances or the lists passed in, always returning new values.

ASCII only.

Validates: Requirements 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12,
2.8, 6.9
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

# ---------------------------------------------------------------------------
# Policy constants
# ---------------------------------------------------------------------------

#: Maximum number of non-done Backlog_Items allowed in the Topic_Frontier.
#: Adding an item that would exceed this bound is rejected. (3.8, 3.9)
MAX_BACKLOG: int = 10_000

#: Maximum number of adjacent topics spawned per seeded topic / engagement
#: signal. (3.3, 3.4)
MAX_ADJACENT_PER_SEED: int = 5

#: A Backlog_Item is either awaiting seeding ("pending") or already
#: successfully seeded ("done"). Done items are excluded from selection.
Status = Literal["pending", "done"]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BacklogItem:
    """A single queued topic in the Topic_Frontier.

    Attributes:
        topic: The topic slug. This is the item's identity, used both for
            dedupe and as the deterministic lexicographic tie-break key.
        level: The Content_Level ("beginner" | "intermediate" | "advanced").
        priority: Demand/coverage-gap derived priority. Invariant: the value
            is in the inclusive range [0.0, 1.0].
        status: Either "pending" or "done".
    """

    topic: str
    level: str
    priority: float
    status: Status


# ---------------------------------------------------------------------------
# Priority helpers
# ---------------------------------------------------------------------------

def clamp_priority(p: float) -> float:
    """Clamp a priority value to the inclusive range [0.0, 1.0].

    Args:
        p: A raw priority value.

    Returns:
        ``p`` constrained to [0.0, 1.0].

    Validates: Requirements 3.5
    """
    if p < 0.0:
        return 0.0
    if p > 1.0:
        return 1.0
    return p


def derive_priority(demand: float, coverage_gap: float) -> float:
    """Combine learner-demand and grade-coverage-gap signals into [0.0, 1.0].

    Both signals are first clamped to [0.0, 1.0] so out-of-range inputs cannot
    skew the result, then combined with an equal-weight average and clamped
    again to guarantee the [0.0, 1.0] invariant. Pure and deterministic: the
    same inputs always yield the same output.

    Args:
        demand: Learner-demand signal (higher means more requested).
        coverage_gap: Grade-coverage-gap signal (higher means less covered).

    Returns:
        A priority value in the inclusive range [0.0, 1.0].

    Validates: Requirements 3.5
    """
    d = clamp_priority(demand)
    g = clamp_priority(coverage_gap)
    return clamp_priority((d + g) / 2.0)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def prioritize(items: list[BacklogItem]) -> list[BacklogItem]:
    """Return pending items sorted highest-priority-first, slug-asc on ties.

    Only items whose status is "pending" are included; "done" items are
    excluded from selection. The ordering key is ``(-priority, topic)`` so
    that the highest priority comes first and, among equal priorities, the
    lexicographically smallest topic slug comes first as a deterministic
    tie-break. The input list and its items are never mutated.

    Args:
        items: The current backlog (any mix of pending/done items).

    Returns:
        A new list of pending items in selection order.

    Validates: Requirements 3.6, 3.10, 3.12
    """
    pending = [item for item in items if item.status == "pending"]
    return sorted(pending, key=lambda item: (-item.priority, item.topic))


def select_next(items: list[BacklogItem]) -> BacklogItem | None:
    """Return the next pending item to seed, or None when none are pending.

    The next item is the first of :func:`prioritize` -- the highest-priority
    pending item, breaking ties by the lexicographically smallest topic slug.
    Done items are never returned.

    Args:
        items: The current backlog.

    Returns:
        The highest-priority pending ``BacklogItem``, or None when no pending
        item exists.

    Validates: Requirements 3.6, 3.10
    """
    ordered = prioritize(items)
    return ordered[0] if ordered else None


# ---------------------------------------------------------------------------
# Enqueue / growth
# ---------------------------------------------------------------------------

def _non_done_count(items: list[BacklogItem]) -> int:
    """Count items whose status is not "done"."""
    return sum(1 for item in items if item.status != "done")


def enqueue(items: list[BacklogItem], candidate: BacklogItem,
            max_backlog: int = MAX_BACKLOG) -> list[BacklogItem]:
    """Add ``candidate`` to the backlog unless it is a dupe or the cap is hit.

    Rules (all pure; the input list/items are never mutated):

    - Dedupe: if a non-done item with the same ``topic`` already exists, the
      backlog is returned unchanged and no duplicate is added. (3.7)
    - Bound: if the count of non-done items is already at ``max_backlog``,
      the candidate is rejected and all existing non-done items are retained
      unchanged. (3.8, 3.9)
    - Otherwise the candidate is appended to a new list.

    Args:
        items: The current backlog.
        candidate: The item to add.
        max_backlog: The maximum number of non-done items allowed. Defaults to
            :data:`MAX_BACKLOG`.

    Returns:
        A new backlog list reflecting the (possibly no-op) enqueue.

    Validates: Requirements 3.7, 3.8, 3.9
    """
    for item in items:
        if item.status != "done" and item.topic == candidate.topic:
            # Dedupe: leave the existing non-done item unchanged.
            return list(items)

    if _non_done_count(items) >= max_backlog:
        # Bound reached: reject the candidate, retain existing items.
        return list(items)

    return [*items, candidate]


def enqueue_adjacent(items: list[BacklogItem], candidates: list[BacklogItem],
                     cap: int = MAX_ADJACENT_PER_SEED) -> list[BacklogItem]:
    """Enqueue at most ``cap`` candidates, applying :func:`enqueue` to each.

    At most ``cap`` candidates (the first ``cap`` in order) are considered;
    each is routed through :func:`enqueue` so dedupe and the backlog bound are
    honored. This bounds adjacency growth to ``cap`` new items per seeded topic
    or engagement signal. Pure; the inputs are never mutated.

    Args:
        items: The current backlog.
        candidates: Proposed adjacent items, in priority order.
        cap: The maximum number of candidates to consider. Defaults to
            :data:`MAX_ADJACENT_PER_SEED`.

    Returns:
        A new backlog list with the accepted candidates added.

    Validates: Requirements 3.3, 3.4
    """
    result = list(items)
    if cap <= 0:
        return result
    for candidate in candidates[:cap]:
        result = enqueue(result, candidate)
    return result


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------

def mark_done(items: list[BacklogItem], topic: str) -> list[BacklogItem]:
    """Return a new backlog with the matching item's status set to "done".

    The item whose ``topic`` equals ``topic`` is replaced by a copy with
    ``status="done"`` so it is excluded from all future selection. Items are
    immutable (frozen dataclass), so a new instance is produced via
    ``dataclasses.replace`` and the originals are left untouched. When no item
    matches, the backlog is returned unchanged.

    Args:
        items: The current backlog.
        topic: The slug of the item to mark done.

    Returns:
        A new backlog list with the matching item marked done.

    Validates: Requirements 3.10
    """
    result: list[BacklogItem] = []
    for item in items:
        if item.topic == topic and item.status != "done":
            result.append(replace(item, status="done"))
        else:
            result.append(item)
    return result


def apply_seed_outcome(items: list[BacklogItem], topic: str,
                       success: bool) -> list[BacklogItem]:
    """Apply a seeding outcome to the backlog.

    On success the item is marked done (:func:`mark_done`) and thereafter
    excluded from selection. On failure the backlog is returned unchanged so
    the item retains its pre-seeding status and remains eligible for a later
    retry. Pure; the inputs are never mutated.

    Args:
        items: The current backlog.
        topic: The slug of the seeded item.
        success: Whether seeding succeeded.

    Returns:
        A new backlog list reflecting the outcome.

    Validates: Requirements 2.8, 3.10, 3.11
    """
    if success:
        return mark_done(items, topic)
    return list(items)
