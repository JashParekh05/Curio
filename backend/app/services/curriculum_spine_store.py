"""Curriculum_Spine persistence: thin, best-effort I/O shell over the pure core.

This module is the I/O shell paired with the pure decision core in
``curriculum_spine.py``. Mirroring the convention of ``arc_unifier_store.py`` /
``coverage_view_store.py`` / ``arc_backfill_runner.py`` and the design's "pure
decision core, thin best-effort I/O shell" principle, it holds ONLY the Supabase
reads/writes; every spine decision (duplicate-slug and cycle rejection,
serialization) lives in ``curriculum_spine`` and is merely applied here.

What it does:

- **Reload the spine identically after restart** (Req 5.1): reads the
  ``curriculum_spine_nodes`` and ``curriculum_spine_edges`` rows and reconstructs
  the same Spine_Nodes (slug, Content_Level, estimated duration) and Spine_Edge
  endpoints via the pure ``deserialize_*`` functions.
- **Exclude Pruned_Topics** (Req 5.7): a loaded node whose Topic is archived
  (``topics.archived``), and every edge incident to it, is dropped from the
  returned spine.
- **Admit a newly Arc_Complete, non-pruned Topic** (Req 5.8): a Topic is
  Spine_Node-eligible iff ``topics.arc_complete`` is set and the Topic is not
  archived; registration of an ineligible Topic is refused.
- **Remove a pruned Topic from the spine** (Req 5.9): on prune it applies the
  pure ``remove_topic`` to the loaded graph and deletes the Topic's node row and
  every incident edge row.

The ``curriculum_spine_nodes`` / ``curriculum_spine_edges`` tables and the
``topics.archived`` / ``topics.arc_complete`` columns are added by the Phase 4
additive migration (``migration_content_revamp.sql``). This shell is written to
use them and degrades gracefully (logs and returns a safe default) when they are
not yet present, exactly like ``coverage_view_store.py`` -- so a spine pass can
never block or crash the request path.

ASCII only.

Validates: Requirements 5.1, 5.7, 5.8, 5.9
"""
from __future__ import annotations

import logging

from app.db.supabase import get_client
from app.services.curriculum_spine import (
    AddEdgeResult,
    SpineEdge,
    SpineNode,
    add_edge,
    add_node,
    deserialize_edges,
    deserialize_nodes,
    remove_topic,
    serialize_edges,
    serialize_nodes,
)

logger = logging.getLogger(__name__)

_NODES_TABLE = "curriculum_spine_nodes"
_EDGES_TABLE = "curriculum_spine_edges"
_TOPICS_TABLE = "topics"


# ---------------------------------------------------------------------------
# Reads (best-effort; every read degrades to a safe default on any failure)
# ---------------------------------------------------------------------------

def _read_node_rows(db) -> list[dict]:
    """Read the persisted Spine_Node rows. Best-effort: empty list on failure."""
    try:
        res = (
            db.table(_NODES_TABLE)
            .select("topic_slug, content_level, est_minutes")
            .execute()
        )
        return list(res.data or [])
    except Exception as exc:
        logger.warning("[curriculum_spine] read nodes failed: %s", exc)
        return []


def _read_edge_rows(db) -> list[dict]:
    """Read the persisted Spine_Edge rows. Best-effort: empty list on failure."""
    try:
        res = db.table(_EDGES_TABLE).select("prerequisite, dependent").execute()
        return list(res.data or [])
    except Exception as exc:
        logger.warning("[curriculum_spine] read edges failed: %s", exc)
        return []


def _read_pruned_slugs(db) -> set[str]:
    """Read the set of Pruned_Topic slugs from ``topics.archived``.

    The ``archived`` column is added by the Phase 4 additive migration; until it
    exists the read fails and an empty set is returned, so every Topic is treated
    as non-pruned. Best-effort and read-only.
    """
    try:
        res = db.table(_TOPICS_TABLE).select("slug, archived").execute()
        return {
            row["slug"]
            for row in (res.data or [])
            if row.get("slug") is not None and bool(row.get("archived"))
        }
    except Exception as exc:
        logger.warning("[curriculum_spine] read topics.archived failed: %s", exc)
        return set()


def load_spine(db=None) -> tuple[list[SpineNode], list[SpineEdge]]:
    """Reload the Curriculum_Spine, excluding every Pruned_Topic.

    Reads the persisted node and edge rows and reconstructs them identically via
    the pure ``deserialize_*`` functions, so after a process restart each
    previously registered Spine_Node returns the same Topic slug, Content_Level,
    and prerequisite Spine_Edge endpoints recorded before the restart (Req 5.1).
    Every Pruned_Topic (``topics.archived``) is excluded, along with every edge
    incident to a pruned node (Req 5.7). Strictly read-only and best-effort: any
    DB error degrades to an empty spine rather than raising.

    Args:
        db: Optional Supabase client (injected in tests); defaults to the shared
            client.

    Returns:
        A ``(nodes, edges)`` pair for the non-pruned spine.

    Validates: Requirements 5.1, 5.7
    """
    if db is None:
        db = get_client()

    nodes = deserialize_nodes(_read_node_rows(db))
    edges = deserialize_edges(_read_edge_rows(db))
    pruned = _read_pruned_slugs(db)

    if not pruned:
        return nodes, edges

    # Drop every pruned node and every edge incident to one (Req 5.7).
    nodes = [node for node in nodes if node.topic_slug not in pruned]
    edges = [
        edge
        for edge in edges
        if edge.prerequisite not in pruned and edge.dependent not in pruned
    ]
    return nodes, edges


# ---------------------------------------------------------------------------
# Eligibility (Req 5.8 / 5.7)
# ---------------------------------------------------------------------------

def is_spine_eligible(topic_slug: str, db=None) -> bool:
    """True iff a Topic may be represented as a Spine_Node.

    A Topic is Spine_Node-eligible exactly when it has become Arc_Complete
    (``topics.arc_complete``) and is not a Pruned_Topic (``topics.archived``),
    so a newly Arc_Complete, non-pruned Topic becomes eligible (Req 5.8) while a
    Pruned_Topic never is (Req 5.7). The ``arc_complete`` / ``archived`` columns
    are added by the Phase 4 additive migration; until they exist (or on any
    failure) the Topic is reported ineligible. Best-effort and read-only.

    Args:
        topic_slug: The candidate Topic.
        db: Optional Supabase client (injected in tests).

    Returns:
        True iff the Topic is Arc_Complete and not pruned.

    Validates: Requirements 5.7, 5.8
    """
    if db is None:
        db = get_client()
    try:
        res = (
            db.table(_TOPICS_TABLE)
            .select("slug, archived, arc_complete")
            .eq("slug", topic_slug)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return False
        row = rows[0]
        return bool(row.get("arc_complete")) and not bool(row.get("archived"))
    except Exception as exc:
        logger.warning(
            "[curriculum_spine] is_spine_eligible('%s') failed: %s", topic_slug, exc
        )
        return False


# ---------------------------------------------------------------------------
# Writes (best-effort; degrade gracefully when tables/columns are absent)
# ---------------------------------------------------------------------------

def register_spine_node(node: SpineNode, db=None) -> bool:
    """Register a newly Arc_Complete, non-pruned Topic as a Spine_Node.

    Refuses an ineligible Topic (not Arc_Complete or pruned -- Req 5.7, 5.8) and
    a duplicate slug: the candidate is routed through the pure
    :func:`~app.services.curriculum_spine.add_node` against the loaded nodes, and
    a rejected duplicate leaves the persisted nodes unchanged (Req 5.5). On
    acceptance the node row is upserted via the pure ``serialize_nodes``.
    Best-effort: any DB error is logged and swallowed, returning False.

    Args:
        node: The Spine_Node to register.
        db: Optional Supabase client (injected in tests).

    Returns:
        True iff the node was persisted.

    Validates: Requirements 5.5, 5.7, 5.8
    """
    if db is None:
        db = get_client()

    if not is_spine_eligible(node.topic_slug, db):
        logger.info(
            "[curriculum_spine] node '%s' not eligible (not arc-complete or pruned)",
            node.topic_slug,
        )
        return False

    nodes, _edges = load_spine(db)
    result = add_node(nodes, node)
    if result.rejected_slug is not None:
        logger.info(
            "[curriculum_spine] node '%s' rejected as duplicate slug", node.topic_slug
        )
        return False

    try:
        (row,) = serialize_nodes([node])
        db.table(_NODES_TABLE).upsert(row, on_conflict="topic_slug").execute()
        logger.info("[curriculum_spine] node '%s' registered", node.topic_slug)
        return True
    except Exception as exc:
        logger.warning(
            "[curriculum_spine] persist node '%s' failed: %s", node.topic_slug, exc
        )
        return False


def register_spine_edge(edge: SpineEdge, db=None) -> AddEdgeResult:
    """Register a prerequisite Spine_Edge unless it introduces a cycle.

    Routes the candidate through the pure
    :func:`~app.services.curriculum_spine.add_edge` against the loaded graph: a
    cycle-creating edge is rejected, the persisted edges are left unchanged, and
    the rejected ``(source, target)`` is reported (Req 5.3, 5.4). On acceptance
    the edge row is upserted via the pure ``serialize_edges``. Best-effort: any
    DB error is logged and swallowed; the pure :class:`AddEdgeResult` is still
    returned.

    Args:
        edge: The Spine_Edge to register.
        db: Optional Supabase client (injected in tests).

    Returns:
        The pure :class:`AddEdgeResult` describing acceptance or rejection.

    Validates: Requirements 5.3, 5.4
    """
    if db is None:
        db = get_client()

    nodes, edges = load_spine(db)
    result = add_edge(nodes, edges, edge)
    if result.rejected_edge is not None:
        logger.info(
            "[curriculum_spine] edge %s -> %s rejected (would create a cycle)",
            edge.prerequisite, edge.dependent,
        )
        return result

    try:
        (row,) = serialize_edges([edge])
        db.table(_EDGES_TABLE).upsert(
            row, on_conflict="prerequisite,dependent"
        ).execute()
        logger.info(
            "[curriculum_spine] edge %s -> %s registered",
            edge.prerequisite, edge.dependent,
        )
    except Exception as exc:
        logger.warning(
            "[curriculum_spine] persist edge %s -> %s failed: %s",
            edge.prerequisite, edge.dependent, exc,
        )
    return result


def prune_from_spine(topic_slug: str, db=None) -> bool:
    """Remove a pruned Topic's Spine_Node and every incident Spine_Edge.

    When a Topic becomes a Pruned_Topic its node and every edge for which it is
    the prerequisite or the dependent are removed from the spine (Req 5.9). The
    pure :func:`~app.services.curriculum_spine.remove_topic` decides the resulting
    graph; this shell deletes the matching ``curriculum_spine_nodes`` row and the
    incident ``curriculum_spine_edges`` rows. Best-effort: any DB error is logged
    and swallowed.

    Args:
        topic_slug: The pruned Topic to remove from the spine.
        db: Optional Supabase client (injected in tests).

    Returns:
        True iff every delete that was attempted succeeded.

    Validates: Requirements 5.9
    """
    if db is None:
        db = get_client()

    # The pure core determines exactly which rows should remain; the deletes
    # below realize that result against the two spine tables.
    nodes, edges = load_spine(db)
    remove_topic(nodes, edges, topic_slug)

    ok = True
    try:
        db.table(_NODES_TABLE).delete().eq("topic_slug", topic_slug).execute()
    except Exception as exc:
        logger.warning(
            "[curriculum_spine] delete node '%s' failed: %s", topic_slug, exc
        )
        ok = False

    # Incident edges: the Topic as prerequisite, then as dependent (two deletes
    # rather than one OR, which the query builder does not express).
    for column in ("prerequisite", "dependent"):
        try:
            db.table(_EDGES_TABLE).delete().eq(column, topic_slug).execute()
        except Exception as exc:
            logger.warning(
                "[curriculum_spine] delete incident edges (%s='%s') failed: %s",
                column, topic_slug, exc,
            )
            ok = False

    if ok:
        logger.info("[curriculum_spine] topic '%s' removed from spine", topic_slug)
    return ok
