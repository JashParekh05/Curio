"""Spine_Router pure decision core (Phase 3, Req 6).

The Spine_Router resolves a learner query to an ordered Learning_Path of
existing Spine_Nodes drawn from the Curriculum_Spine, instead of inventing
ephemeral Topics per query (the job ``curriculum_agent.run_curriculum`` does
today). Given a per-node Match_Score in the inclusive range 0.0 to 1.0, it
selects every Spine_Node whose Match_Score is at or above the configurable
Spine_Match_Threshold, orders the selection so prerequisites precede dependents,
caps the path at 50 distinct nodes, and is fully deterministic for identical
inputs (Req 6.1, 6.2, 6.4, 6.5, 6.6, 6.10, 6.11).

Following the repository convention (see ``curriculum_spine.py`` /
``reseed_prioritizer.py`` / ``coverage_view.py``), this module holds ONLY the
pure decision logic: no DB, no clock, no globals read, no embedding computation.
The Match_Score itself is supplied by the thin shell ``spine_router_runner.py``,
which computes a name-embedding cosine (reusing the resolver embedding) and acts
on the result -- enqueuing one Backlog_Item when nothing matched. Every function
here is deterministic in its inputs and free of side effects: it never mutates
its inputs and makes no persistent state change, so it is exhaustively
property-testable offline.

Totality: ``route`` never raises. On an invalid query (empty, whitespace-only,
or longer than 512 characters) or a cyclic matched set it returns a *rejection*
result and flags no Topic_Frontier enqueue, so the shell leaves the frontier
untouched (Req 6.8, 6.9). The cycle-detection and topological-ordering decisions
are reused from ``curriculum_spine`` rather than reimplemented.

ASCII only.

Validates: Requirements 6.1, 6.2, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9, 6.10, 6.11
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.curriculum_spine import (
    SpineEdge,
    SpineNode,
    add_edge,
    has_cycle,
    topo_order,
)

# ---------------------------------------------------------------------------
# Policy constants
# ---------------------------------------------------------------------------

#: The Match_Score at or above which a Spine_Node is considered a match for a
#: learner query. Default 0.75; valid inclusive range [0.0, 1.0]. (Req 6.1)
DEFAULT_SPINE_MATCH_THRESHOLD: float = 0.75

#: The maximum number of distinct Spine_Nodes a Learning_Path may contain. A
#: matched selection larger than this is truncated to the first 50 in
#: prerequisite order. (Req 6.4)
MAX_PATH_NODES: int = 50

#: The maximum allowed learner-query length, in characters. A longer query is
#: rejected. (Req 6.9)
MAX_QUERY_CHARS: int = 512


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ScoredNode:
    """A Spine_Node paired with its computed Match_Score for one query.

    Attributes:
        node: The candidate Spine_Node (the caller supplies only non-pruned
            nodes -- Req 6.3).
        match_score: The Match_Score between the query and the node, in the
            inclusive range [0.0, 1.0] (Req 6.1).
    """

    node: SpineNode
    match_score: float


@dataclass(frozen=True)
class RouteResult:
    """Outcome of a :func:`route` resolution.

    Attributes:
        path: The ordered Learning_Path: between 1 and 50 distinct Spine_Nodes
            when any path is returned (Req 6.4), empty on a rejection.
        enqueue_unmatched: True iff no Spine_Node met the threshold, signalling
            the shell to enqueue exactly one Backlog_Item for the unmatched query
            (Req 6.7). Always False on a rejection so the frontier is left
            unchanged (Req 6.8, 6.9).
        rejection: None when the resolution succeeded, else one of
            ``"empty_query"``, ``"query_too_long"`` (Req 6.9), or ``"cyclic"``
            (Req 6.8).
        cyclic_edge: The ``(source, target)`` slugs identifying the cyclic
            prerequisite relationship when ``rejection == "cyclic"``, else None
            (Req 6.8).
    """

    path: tuple[SpineNode, ...]
    enqueue_unmatched: bool
    rejection: str | None
    cyclic_edge: tuple[str, str] | None


# ---------------------------------------------------------------------------
# Query validation
# ---------------------------------------------------------------------------

def validate_query(query: str) -> str | None:
    """Validate a learner query, returning a rejection reason or None.

    A query is rejected when it is empty or whitespace-only
    (``"empty_query"``) or when it exceeds 512 characters (``"query_too_long"``)
    (Req 6.9). Otherwise None is returned. Pure and total.

    Args:
        query: The raw learner query.

    Returns:
        None when the query is valid, else the rejection reason string.

    Validates: Requirements 6.9
    """
    if query is None or not query.strip():
        return "empty_query"
    if len(query) > MAX_QUERY_CHARS:
        return "query_too_long"
    return None


# ---------------------------------------------------------------------------
# Selection helpers
# ---------------------------------------------------------------------------

def _dedupe_scored(scored: list[ScoredNode]) -> dict[str, ScoredNode]:
    """Collapse scored nodes to one per Topic slug, keeping the highest score.

    The Spine_Node slug is the stable identity (Req 6.5); a slug appearing more
    than once is reduced to a single entry carrying its maximum Match_Score so
    the slug matches the threshold iff any of its entries does. Ties on score
    keep the first occurrence, which is made deterministic by the caller sorting
    by slug before dedupe. Pure: the input is never mutated.
    """
    best: dict[str, ScoredNode] = {}
    for sn in scored:
        slug = sn.node.topic_slug
        current = best.get(slug)
        if current is None or sn.match_score > current.match_score:
            best[slug] = sn
    return best


def _find_cyclic_edge(
    nodes: list[SpineNode], edges: list[SpineEdge]
) -> tuple[str, str] | None:
    """Return one ``(source, target)`` edge that closes a cycle, or None.

    Replays the edges in a deterministic ``(prerequisite, dependent)`` order
    through the pure :func:`~app.services.curriculum_spine.add_edge`; the first
    edge whose addition is rejected for introducing a cycle is the reported
    cyclic relationship (Req 6.8). Pure: reuses the spine core rather than
    reimplementing cycle detection.
    """
    ordered = sorted(edges, key=lambda e: (e.prerequisite, e.dependent))
    accepted: list[SpineEdge] = []
    for edge in ordered:
        result = add_edge(nodes, accepted, edge)
        if result.rejected_edge is not None:
            return result.rejected_edge
        accepted = list(result.edges)
    return None


def _closest_node(scored: list[ScoredNode]) -> tuple[SpineNode, ...]:
    """Return the single closest available node as a 1-node path, or empty.

    The closest node is the one with the highest Match_Score, breaking ties by
    ascending Topic slug so the choice is deterministic (Req 6.5, 6.10). When no
    node is available the path is empty. Used for the no-match case (Req 6.7).
    """
    if not scored:
        return ()
    best = min(scored, key=lambda sn: (-sn.match_score, sn.node.topic_slug))
    return (best.node,)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def route(
    query: str,
    scored: list[ScoredNode],
    edges: list[SpineEdge],
    threshold: float = DEFAULT_SPINE_MATCH_THRESHOLD,
) -> RouteResult:
    """Resolve a learner query to an ordered Learning_Path of Spine_Nodes.

    The resolution proceeds as:

    1. **Validate the query.** An empty, whitespace-only, or over-512-character
       query is rejected with no Topic_Frontier mutation (``enqueue_unmatched``
       stays False) (Req 6.9).
    2. **Select matches.** Every distinct Spine_Node whose Match_Score is greater
       than or equal to ``threshold`` is selected; a slug seen more than once is
       collapsed to its highest score (Req 6.1, 6.6). The caller supplies only
       non-pruned nodes, so no Pruned_Topic can appear (Req 6.3).
    3. **No match -> closest path + enqueue.** When nothing meets the threshold,
       flag exactly one Backlog_Item enqueue and return the closest available
       single-node path (Req 6.7).
    4. **Cyclic matched set -> reject.** When the matched nodes' prerequisite
       edges contain a cycle, reject the resolution, record the cyclic
       ``(source, target)``, and flag no enqueue so the frontier is unchanged
       (Req 6.8).
    5. **Order and bound.** Otherwise topologically order the matched nodes so
       every prerequisite precedes its dependent, breaking position ties by
       ascending stable slug (Req 6.2, 6.5), and cap the result at 50 distinct
       nodes (Req 6.4).

    Deterministic for identical inputs (Req 6.10) and pure: it never mutates its
    arguments and makes no persistent state change (Req 6.11). Never raises.

    Args:
        query: The learner query.
        scored: The non-pruned Spine_Nodes each paired with its Match_Score.
        edges: The Curriculum_Spine prerequisite edges.
        threshold: The Spine_Match_Threshold (default 0.75, valid [0.0, 1.0]).

    Returns:
        A :class:`RouteResult` describing the path, any enqueue flag, and any
        rejection.

    Validates: Requirements 6.1, 6.2, 6.4, 6.5, 6.6, 6.7, 6.8, 6.9, 6.10, 6.11
    """
    rejection = validate_query(query)
    if rejection is not None:
        # Invalid query: reject and leave the Topic_Frontier untouched (Req 6.9).
        return RouteResult(
            path=(), enqueue_unmatched=False, rejection=rejection, cyclic_edge=None
        )

    # Collapse to one entry per slug (highest score) for distinctness (Req 6.4).
    by_slug = _dedupe_scored(scored)

    matched_slugs = {
        slug for slug, sn in by_slug.items() if sn.match_score >= threshold
    }

    if not matched_slugs:
        # No Spine_Node met the threshold: enqueue one Backlog_Item for the
        # unmatched query and return the closest available path (Req 6.7).
        return RouteResult(
            path=_closest_node(list(by_slug.values())),
            enqueue_unmatched=True,
            rejection=None,
            cyclic_edge=None,
        )

    matched_nodes = [by_slug[slug].node for slug in matched_slugs]
    matched_edges = [
        edge
        for edge in edges
        if edge.prerequisite in matched_slugs and edge.dependent in matched_slugs
    ]

    if has_cycle(matched_nodes, matched_edges):
        # Cyclic matched set: reject, record the relationship, no enqueue
        # so the Topic_Frontier is left unchanged (Req 6.8).
        return RouteResult(
            path=(),
            enqueue_unmatched=False,
            rejection="cyclic",
            cyclic_edge=_find_cyclic_edge(matched_nodes, matched_edges),
        )

    # Prerequisite order with an ascending-slug tiebreak (Req 6.2, 6.5),
    # capped at 50 distinct nodes (Req 6.4).
    ordered = topo_order(matched_nodes, matched_edges)
    return RouteResult(
        path=tuple(ordered[:MAX_PATH_NODES]),
        enqueue_unmatched=False,
        rejection=None,
        cyclic_edge=None,
    )
