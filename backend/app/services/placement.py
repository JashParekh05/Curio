"""Placement pure decision core (Phase 3, Req 9).

Placement maps a learner onto an ordered Learning_Path of Spine_Nodes: it selects
exactly one entry-point node and estimates the time to complete the remaining path.

Following the repository convention (see ``topic_frontier.py`` /
``reseed_prioritizer.py`` / ``coverage_view.py``), this module holds ONLY the pure
decision logic: no DB, no clock, no global reads. Every function is deterministic
in its inputs and free of side effects -- it derives the Placement solely from the
supplied Learning_Path, prerequisite edges, and learner level, and changes no
Curriculum_Spine state (Req 9.5, 9.6).

The Curriculum_Spine core (``curriculum_spine.py``) is being built in parallel and
owns the canonical ``SpineNode`` / ``SpineEdge`` definitions. To keep this core
importable offline and independently testable, the node and edge shapes Placement
consumes are modeled here as minimal, structurally-equivalent frozen dataclasses:
a node is a Topic slug plus a derived Content_Level and an estimated duration, and
an edge is a directed prerequisite -> dependent slug pair. They mirror the design's
``SpineNode`` (topic_slug, content_level, est_minutes) and ``SpineEdge``
(prerequisite, dependent).

Entry-point selection (Req 9.1, 9.2): the entry is the earliest node in the
Learning_Path whose Content_Level is at or above the learner's level (using the
ordering beginner < intermediate < advanced), so Spine_Nodes whose Content_Level
is below the learner's level -- material the learner already knows -- are skipped.
When no node reaches the learner's level the entry is the last node. Because the
Spine_Router hands Placement a prerequisite-ordered path (every prerequisite
precedes its dependent), skipping a lower-level prefix never strands a needed
prerequisite, so the entry is prerequisite-consistent. Where the learner's level
is absent or outside the valid range, the entry is the first node instead
(Req 9.4). Remaining time is the sum of the estimated durations at or after the
entry, in whole minutes >= 0 (Req 9.3). An empty Learning_Path yields no entry and
a "placement not possible" indication, with no spine mutation (Req 9.5).

ASCII only.

Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5, 9.6
"""
from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Policy constants
# ---------------------------------------------------------------------------

#: The defined valid Content_Level band values for a learner, ordered from
#: lowest to highest. A learner level outside this set (or absent) triggers the
#: first-node fallback (Req 9.4).
VALID_LEVELS: tuple[str, ...] = ("beginner", "intermediate", "advanced")

#: The Content_Level ordering used to compare a node's level against the
#: learner's level: beginner < intermediate < advanced (Req 9.2). A node whose
#: Content_Level is unrecognized is treated as the lowest band so it is never
#: skipped over a learner who has a recognized level.
_LEVEL_RANK: dict[str, int] = {level: rank for rank, level in enumerate(VALID_LEVELS)}


# ---------------------------------------------------------------------------
# Data model (mirrors curriculum_spine.SpineNode / SpineEdge)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SpineNode:
    """One Topic in an ordered Learning_Path.

    Attributes:
        topic_slug: The Topic's stable slug; node identity within a path.
        content_level: The Topic's derived Content_Level band.
        est_minutes: The Topic's estimated duration in whole minutes (>= 0); used
            to estimate the remaining time of a Learning_Path (Req 9.3).
    """

    topic_slug: str
    content_level: str
    est_minutes: int


@dataclass(frozen=True)
class SpineEdge:
    """A directed prerequisite relationship between two Topics.

    An edge from ``prerequisite`` to ``dependent`` means the prerequisite Topic
    must be learned before the dependent Topic (A is a prerequisite of B: A -> B).

    Attributes:
        prerequisite: The source Topic slug (the prerequisite).
        dependent: The target Topic slug (depends on the prerequisite).
    """

    prerequisite: str
    dependent: str


@dataclass(frozen=True)
class PlacementResult:
    """The result of placing a learner onto a Learning_Path.

    Attributes:
        entry_index: The index into the ordered Learning_Path at which the learner
            is placed, or None when placement is not possible (Req 9.1, 9.5).
        est_minutes: The estimated time to complete the Learning_Path from the
            entry point, computed as the sum of the estimated durations at or after
            the entry, in whole minutes >= 0 (Req 9.3). Zero when not possible.
        not_possible: True iff the Learning_Path contains zero Spine_Nodes, so no
            entry point could be selected (Req 9.5).
    """

    entry_index: int | None
    est_minutes: int
    not_possible: bool


# ---------------------------------------------------------------------------
# Entry-point selection (level-based)
# ---------------------------------------------------------------------------

def _select_entry_index(path: list[SpineNode], learner_level: str) -> int:
    """Select the level-appropriate entry index for a valid learner level.

    Returns the index of the earliest Spine_Node whose Content_Level is at or
    above the learner's level, so that lower-level nodes -- material the learner
    already knows -- are skipped (Req 9.2). Content_Levels are compared via
    :data:`_LEVEL_RANK` (beginner < intermediate < advanced); a node whose
    Content_Level is unrecognized is treated as the lowest band. When no node
    reaches the learner's level (the learner already knows every Topic on the
    path), the last node is selected so a non-empty path always yields exactly
    one entry (Req 9.1).

    The Spine_Router hands Placement a prerequisite-ordered path, so skipping a
    lower-level prefix never strands a prerequisite of a kept node; the entry is
    therefore prerequisite-consistent.
    """
    target_rank = _LEVEL_RANK[learner_level]
    for index, node in enumerate(path):
        if _LEVEL_RANK.get(node.content_level, 0) >= target_rank:
            return index
    return len(path) - 1


# ---------------------------------------------------------------------------
# Remaining-time estimate
# ---------------------------------------------------------------------------

def _remaining_minutes(path: list[SpineNode], entry_index: int) -> int:
    """Sum the estimated durations of the nodes at or after the entry.

    The remaining time is the sum of ``est_minutes`` for every Spine_Node ordered
    at or after the entry point, expressed in whole minutes >= 0 (Req 9.3). Each
    node's contribution is floored at 0 so the total is never negative.
    """
    total = 0
    for node in path[entry_index:]:
        total += node.est_minutes if node.est_minutes > 0 else 0
    return total


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------

def place(
    path: list[SpineNode],
    edges: list[SpineEdge],
    learner_level: str | None,
) -> PlacementResult:
    """Place a learner onto an ordered Learning_Path.

    When the Learning_Path contains at least one Spine_Node, selects exactly one
    entry-point node (Req 9.1). When the learner's level is one of
    :data:`VALID_LEVELS`, the entry is the earliest node whose Content_Level is at
    or above the learner's level (ordering beginner < intermediate < advanced), so
    lower-level nodes -- material the learner already knows -- are skipped; when no
    node reaches the learner's level the entry is the last node (Req 9.2). Because
    the path is prerequisite-ordered, this entry is prerequisite-consistent. When
    the learner's level is absent (None) or outside the valid range, the entry is
    the first node of the path instead (Req 9.4). The estimated time to complete
    the path is the sum of the estimated durations at or after the entry, in whole
    minutes >= 0 (Req 9.3).

    An empty Learning_Path yields no entry point and a "placement not possible"
    indication, leaving Curriculum_Spine state unchanged (Req 9.5).

    Pure and deterministic: identical inputs always produce identical output, and
    no external service is accessed and no state is changed (Req 9.6).

    Args:
        path: The ordered Learning_Path of Spine_Nodes.
        edges: The prerequisite edges among the path's Topics. Retained for
            interface compatibility; the path is already prerequisite-ordered by
            the Spine_Router, so level-based entry selection is itself
            prerequisite-consistent and does not need to re-derive the ordering.
        learner_level: The learner's Content_Level, or None when absent.

    Returns:
        The :class:`PlacementResult` describing the entry point and remaining time.

    Validates: Requirements 9.1, 9.2, 9.3, 9.4, 9.5, 9.6
    """
    if not path:
        return PlacementResult(entry_index=None, est_minutes=0, not_possible=True)

    if learner_level is None or learner_level not in VALID_LEVELS:
        entry_index = 0
    else:
        entry_index = _select_entry_index(path, learner_level)

    return PlacementResult(
        entry_index=entry_index,
        est_minutes=_remaining_minutes(path, entry_index),
        not_possible=False,
    )
