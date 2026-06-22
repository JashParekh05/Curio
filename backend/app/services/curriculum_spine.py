"""Curriculum_Spine pure decision core (Phase 3, Req 5).

The Curriculum_Spine is the persistent, dependency-ordered graph of stable
Topics that learning paths route through. A Spine_Node is a single Topic
(carrying its slug, derived Content_Level, and estimated duration); a Spine_Edge
is a directed prerequisite relationship from a prerequisite Topic A to a
dependent Topic B (``A -> B`` means A is a prerequisite of B -- Req 5.2). The
graph is acyclic by construction (Req 5.3).

Following the repository convention (see ``topic_frontier.py`` /
``coverage_view.py``), this module holds ONLY the pure decision logic: no DB, no
clock, no globals read. Every function is deterministic in its inputs and free
of side effects -- it never mutates the inputs, always returning new values, and
makes no persistent state change (Req 5.10). The thin, best-effort I/O shell
that persists and reloads the spine lives in ``curriculum_spine_store.py``.

The cycle-detection and topological-ordering decisions, the duplicate-slug and
cycle rejections, and the serialize/deserialize round-trip are all expressed
here as pure functions so they are exhaustively property-testable offline.

ASCII only.

Validates: Requirements 5.2, 5.3, 5.4, 5.5, 5.6, 5.9, 5.10
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SpineNode:
    """A single Topic recorded in the Curriculum_Spine.

    Attributes:
        topic_slug: The Topic slug. This is the node's identity, unique per
            spine (Req 5.5), and the deterministic ascending tiebreak key
            (Req 5.6).
        content_level: The Topic's derived Content_Level ("beginner" |
            "intermediate" | "advanced"), preserved across a reload (Req 5.1).
        est_minutes: The Topic's estimated duration in whole minutes (>= 0),
            consumed by Placement.
    """

    topic_slug: str
    content_level: str
    est_minutes: int


@dataclass(frozen=True)
class SpineEdge:
    """A directed prerequisite relationship within the Curriculum_Spine.

    An edge records that ``prerequisite`` is a prerequisite of ``dependent``,
    represented as a directed edge from the Spine_Node for ``prerequisite`` to
    the Spine_Node for ``dependent`` (Req 5.2).

    Attributes:
        prerequisite: The source Topic slug (taught first).
        dependent: The target Topic slug (depends on the prerequisite).
    """

    prerequisite: str
    dependent: str


@dataclass(frozen=True)
class AddNodeResult:
    """Outcome of an :func:`add_node` attempt.

    Attributes:
        nodes: The resulting node tuple. Unchanged from the input when the
            candidate was rejected (Req 5.5).
        rejected_slug: The candidate's slug when it was rejected as a duplicate,
            else None.
    """

    nodes: tuple[SpineNode, ...]
    rejected_slug: str | None


@dataclass(frozen=True)
class AddEdgeResult:
    """Outcome of an :func:`add_edge` attempt.

    Attributes:
        nodes: The node tuple, always unchanged by an edge add.
        edges: The resulting edge tuple. Unchanged from the input when the
            candidate was rejected for introducing a cycle (Req 5.4).
        rejected_edge: The ``(source, target)`` slugs of the candidate when it
            was rejected for introducing a cycle, else None (Req 5.4).
    """

    nodes: tuple[SpineNode, ...]
    edges: tuple[SpineEdge, ...]
    rejected_edge: tuple[str, str] | None


# ---------------------------------------------------------------------------
# Node registration
# ---------------------------------------------------------------------------

def add_node(nodes: list[SpineNode], candidate: SpineNode) -> AddNodeResult:
    """Register a Spine_Node, rejecting a duplicate Topic slug.

    When no existing node shares the candidate's ``topic_slug`` the candidate is
    appended to a new node tuple. When a node with the same slug already exists
    the candidate is rejected, the existing nodes are left unchanged, and the
    rejected slug is recorded, so the spine holds at most one Spine_Node per
    Topic slug (Req 5.5). Pure: the input list and its items are never mutated.

    Args:
        nodes: The current Spine_Nodes.
        candidate: The node to register.

    Returns:
        An :class:`AddNodeResult` with the resulting nodes and any rejected slug.

    Validates: Requirements 5.5
    """
    for node in nodes:
        if node.topic_slug == candidate.topic_slug:
            return AddNodeResult(nodes=tuple(nodes), rejected_slug=candidate.topic_slug)
    return AddNodeResult(nodes=(*nodes, candidate), rejected_slug=None)


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------

def _adjacency(
    nodes: list[SpineNode], edges: list[SpineEdge]
) -> dict[str, set[str]]:
    """Build a slug -> set-of-dependents adjacency map over nodes and edges.

    Every node slug and every slug referenced by an edge is a key, so a cycle
    formed purely by edges (even among slugs without a registered node) is still
    detectable.
    """
    adjacency: dict[str, set[str]] = {}
    for node in nodes:
        adjacency.setdefault(node.topic_slug, set())
    for edge in edges:
        adjacency.setdefault(edge.prerequisite, set())
        adjacency.setdefault(edge.dependent, set())
        adjacency[edge.prerequisite].add(edge.dependent)
    return adjacency


def has_cycle(nodes: list[SpineNode], edges: list[SpineEdge]) -> bool:
    """Return True iff the directed graph of edges contains a cycle (Req 5.3).

    Runs an iterative three-color depth-first search over the adjacency built
    from ``nodes`` and ``edges``; neighbours and start nodes are visited in
    ascending slug order so the traversal is deterministic. A self-loop
    (``prerequisite == dependent``) is a cycle. Pure and deterministic: identical
    inputs always yield identical output and no state is changed (Req 5.10).

    Args:
        nodes: The current Spine_Nodes.
        edges: The current Spine_Edges.

    Returns:
        True iff the graph contains at least one directed cycle.

    Validates: Requirements 5.3, 5.10
    """
    adjacency = _adjacency(nodes, edges)
    white, grey, black = 0, 1, 2
    color = {slug: white for slug in adjacency}

    for start in sorted(adjacency):
        if color[start] != white:
            continue
        color[start] = grey
        stack: list[tuple[str, list[str]]] = [
            (start, sorted(adjacency[start]))
        ]
        while stack:
            node_slug, neighbours = stack[-1]
            advanced = False
            while neighbours:
                nxt = neighbours.pop(0)
                if color[nxt] == grey:
                    return True
                if color[nxt] == white:
                    color[nxt] = grey
                    stack.append((nxt, sorted(adjacency[nxt])))
                    advanced = True
                    break
            if not advanced:
                color[node_slug] = black
                stack.pop()
    return False


# ---------------------------------------------------------------------------
# Edge registration
# ---------------------------------------------------------------------------

def add_edge(
    nodes: list[SpineNode], edges: list[SpineEdge], candidate: SpineEdge
) -> AddEdgeResult:
    """Add a Spine_Edge unless it would introduce a directed cycle.

    An identical edge already present is a no-op: the graph is returned unchanged
    with no rejection. Otherwise the candidate is added only when the resulting
    graph stays acyclic (Req 5.3); if adding it would introduce a cycle the
    candidate is rejected, the existing nodes and edges are left unchanged, and
    the rejected ``(source, target)`` slugs are recorded (Req 5.4). Pure: the
    inputs are never mutated.

    Args:
        nodes: The current Spine_Nodes (never changed by an edge add).
        edges: The current Spine_Edges.
        candidate: The edge to add.

    Returns:
        An :class:`AddEdgeResult` with the resulting edges and any rejected edge.

    Validates: Requirements 5.3, 5.4
    """
    nodes_t = tuple(nodes)
    edges_t = tuple(edges)

    for edge in edges:
        if (
            edge.prerequisite == candidate.prerequisite
            and edge.dependent == candidate.dependent
        ):
            # Duplicate edge: accept as a no-op, graph unchanged.
            return AddEdgeResult(nodes=nodes_t, edges=edges_t, rejected_edge=None)

    trial = [*edges, candidate]
    if has_cycle(nodes, trial):
        return AddEdgeResult(
            nodes=nodes_t,
            edges=edges_t,
            rejected_edge=(candidate.prerequisite, candidate.dependent),
        )
    return AddEdgeResult(nodes=nodes_t, edges=tuple(trial), rejected_edge=None)


# ---------------------------------------------------------------------------
# Topological order
# ---------------------------------------------------------------------------

def topo_order(nodes: list[SpineNode], edges: list[SpineEdge]) -> list[SpineNode]:
    """Order Spine_Nodes so every prerequisite precedes every dependent.

    Runs Kahn's algorithm, always emitting the available (in-degree zero) node
    with the smallest Topic slug, so every prerequisite Spine_Node is ordered
    ahead of every Spine_Node that depends on it, each node appears exactly once,
    and nodes with no prerequisite relationship between them are ordered by
    ascending Topic slug as a deterministic tiebreaker (Req 5.6). Only edges
    whose endpoints are both registered nodes constrain the order; duplicate
    edges are collapsed. Pure and deterministic, with no persistent state change
    (Req 5.10).

    Assumes the graph is acyclic (guaranteed by :func:`add_edge` / Req 5.3); any
    nodes left in a cycle are omitted rather than raising.

    Args:
        nodes: The current Spine_Nodes.
        edges: The current Spine_Edges.

    Returns:
        The Spine_Nodes in a deterministic topological order.

    Validates: Requirements 5.6, 5.10
    """
    node_by_slug: dict[str, SpineNode] = {}
    for node in nodes:
        # At most one node per slug; first occurrence wins for determinism.
        node_by_slug.setdefault(node.topic_slug, node)

    present = set(node_by_slug)
    adjacency: dict[str, set[str]] = {slug: set() for slug in present}
    indegree: dict[str, int] = {slug: 0 for slug in present}

    for edge in edges:
        if edge.prerequisite not in present or edge.dependent not in present:
            continue
        if edge.dependent in adjacency[edge.prerequisite]:
            continue  # collapse duplicate edge
        adjacency[edge.prerequisite].add(edge.dependent)
        indegree[edge.dependent] += 1

    available = [slug for slug in present if indegree[slug] == 0]
    heapq.heapify(available)

    order: list[SpineNode] = []
    while available:
        slug = heapq.heappop(available)
        order.append(node_by_slug[slug])
        for dependent in sorted(adjacency[slug]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(available, dependent)
    return order


# ---------------------------------------------------------------------------
# Topic removal
# ---------------------------------------------------------------------------

def remove_topic(
    nodes: list[SpineNode], edges: list[SpineEdge], topic_slug: str
) -> tuple[list[SpineNode], list[SpineEdge]]:
    """Remove a Topic's Spine_Node and every incident Spine_Edge.

    Drops the node whose slug equals ``topic_slug`` and every edge for which that
    slug is the prerequisite or the dependent, while retaining all other nodes
    and edges unchanged (Req 5.9). Used when a Topic becomes a Pruned_Topic
    (Req 5.7). Pure: the inputs are never mutated.

    Args:
        nodes: The current Spine_Nodes.
        edges: The current Spine_Edges.
        topic_slug: The Topic slug to remove.

    Returns:
        A ``(nodes, edges)`` pair with the Topic and its incident edges removed.

    Validates: Requirements 5.9
    """
    new_nodes = [node for node in nodes if node.topic_slug != topic_slug]
    new_edges = [
        edge
        for edge in edges
        if edge.prerequisite != topic_slug and edge.dependent != topic_slug
    ]
    return new_nodes, new_edges


# ---------------------------------------------------------------------------
# Serialization (pure; the round-trip the store relies on)
# ---------------------------------------------------------------------------

def serialize_nodes(nodes: list[SpineNode]) -> list[dict]:
    """Serialize Spine_Nodes to plain rows for persistence.

    Each node becomes a ``{topic_slug, content_level, est_minutes}`` row.
    Paired with :func:`deserialize_nodes` this is an identity round-trip
    (Req 5.1). Pure.
    """
    return [
        {
            "topic_slug": node.topic_slug,
            "content_level": node.content_level,
            "est_minutes": node.est_minutes,
        }
        for node in nodes
    ]


def serialize_edges(edges: list[SpineEdge]) -> list[dict]:
    """Serialize Spine_Edges to plain rows for persistence.

    Each edge becomes a ``{prerequisite, dependent}`` row. Paired with
    :func:`deserialize_edges` this is an identity round-trip (Req 5.1). Pure.
    """
    return [
        {"prerequisite": edge.prerequisite, "dependent": edge.dependent}
        for edge in edges
    ]


def deserialize_nodes(rows: list[dict]) -> list[SpineNode]:
    """Reconstruct Spine_Nodes from persisted rows (inverse of serialize).

    Reproduces each node's Topic slug, Content_Level, and estimated duration
    exactly, so a serialize-then-load round-trip is the identity over the node's
    recorded fields (Req 5.1). Rows missing a ``topic_slug`` are skipped. Pure.
    """
    nodes: list[SpineNode] = []
    for row in rows:
        slug = row.get("topic_slug")
        if slug is None:
            continue
        nodes.append(
            SpineNode(
                topic_slug=slug,
                content_level=row.get("content_level"),
                est_minutes=int(row.get("est_minutes") or 0),
            )
        )
    return nodes


def deserialize_edges(rows: list[dict]) -> list[SpineEdge]:
    """Reconstruct Spine_Edges from persisted rows (inverse of serialize).

    Reproduces each edge's prerequisite and dependent endpoints exactly, so a
    serialize-then-load round-trip is the identity over the edge endpoints
    (Req 5.1). Rows missing an endpoint are skipped. Pure.
    """
    edges: list[SpineEdge] = []
    for row in rows:
        prerequisite = row.get("prerequisite")
        dependent = row.get("dependent")
        if prerequisite is None or dependent is None:
            continue
        edges.append(SpineEdge(prerequisite=prerequisite, dependent=dependent))
    return edges
