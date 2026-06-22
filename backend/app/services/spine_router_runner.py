"""Spine_Router I/O shell: route a learner query through the persistent spine.

This module is the thin, best-effort I/O shell paired with the pure decision
core in ``spine_router.py``. It replaces the per-query Topic *invention* in
``curriculum_agent.run_curriculum``: instead of asking an LLM to fabricate a
fresh learning path for every query, it resolves the query against the existing,
dependency-ordered Curriculum_Spine and returns an ordered path of stable
Spine_Nodes (Req 6.1, 6.2).

Mirroring the convention of ``curriculum_spine_store.py`` /
``arc_backfill_runner.py``, every decision (selection, ordering, cycle and
invalid-query rejection) lives in the pure core; this shell only:

- loads the **non-pruned** spine via ``curriculum_spine_store.load_spine`` (which
  already excludes every Pruned_Topic -- Req 6.3),
- computes a Match_Score per Spine_Node as a name-embedding cosine, reusing the
  same ``embeddings`` helper the topic resolver uses (Req 6.1),
- calls the pure :func:`~app.services.spine_router.route`, and
- on ``enqueue_unmatched`` enqueues **exactly one** Backlog_Item for the unmatched
  query through the existing Topic_Frontier (``topic_frontier.enqueue`` +
  ``backlog_store``) (Req 6.7).

It never mutates the Topic_Frontier on a rejection -- a cyclic matched set or an
invalid query (Req 6.8, 6.9). It is best-effort throughout: every external call
is wrapped so a failure degrades to a safe default and never blocks or crashes
the request path, and it degrades gracefully when the Phase 4 spine tables are
absent (``load_spine`` returns an empty spine).

ASCII only.

Validates: Requirements 6.1, 6.7, 6.8, 6.9
"""
from __future__ import annotations

import logging
import re

from app.services.curriculum_spine_store import load_spine
from app.services.spine_router import (
    DEFAULT_SPINE_MATCH_THRESHOLD,
    RouteResult,
    ScoredNode,
    route,
    validate_query,
)
from app.services.topic_frontier import BacklogItem, enqueue

logger = logging.getLogger(__name__)

# The Content_Level an unmatched-query Backlog_Item carries. The query has no
# level of its own, so it defaults to the same intermediate level the backlog
# shell uses when a row predates the level column.
_DEFAULT_UNMATCHED_LEVEL = "intermediate"

# The priority an unmatched-query Backlog_Item carries -- a neutral mid value, in
# the [0.0, 1.0] BacklogItem invariant.
_DEFAULT_UNMATCHED_PRIORITY = 0.5


# ---------------------------------------------------------------------------
# Slug / text helpers
# ---------------------------------------------------------------------------

def _slug_to_text(slug: str) -> str:
    """Turn a Topic slug into human-readable text for embedding.

    Hyphens and underscores become spaces so ``binary-search-trees`` embeds as
    ``binary search trees`` -- the same shape the resolver embeds Topic names in.
    """
    return re.sub(r"[-_]+", " ", slug or "").strip()


def _query_to_slug(query: str) -> str:
    """Derive a stable Topic slug from an unmatched query for the Backlog_Item.

    Lowercases, collapses any run of non-alphanumeric characters to a single
    hyphen, and trims leading/trailing hyphens, so the same query always maps to
    the same slug and the existing ``topic_frontier.enqueue`` dedupe can suppress
    a duplicate.
    """
    s = re.sub(r"[^a-z0-9]+", "-", (query or "").lower()).strip("-")
    return s or "unmatched-query"


# ---------------------------------------------------------------------------
# Match_Score
# ---------------------------------------------------------------------------

def _score_nodes(query: str, nodes) -> list[ScoredNode]:
    """Compute a Match_Score in [0.0, 1.0] for each Spine_Node.

    The score is the cosine similarity between the query embedding and the
    Spine_Node slug embedding, reusing the shared ``embeddings`` helper the topic
    resolver uses, clamped into [0.0, 1.0] (a negative cosine -> 0.0) to honor the
    Match_Score range (Req 6.1). Best-effort: when embeddings are unavailable or a
    node cannot be embedded its score is 0.0, so a missing model simply yields no
    matches (and the caller enqueues the unmatched query) rather than raising.
    """
    try:
        from app.services.embeddings import cosine_similarity, embed_text, embed_texts
    except Exception as exc:  # pragma: no cover - import guard
        logger.warning("[spine_router] embeddings unavailable: %s", exc)
        return [ScoredNode(node=node, match_score=0.0) for node in nodes]

    qv = None
    try:
        qv = embed_text(query)
    except Exception as exc:
        logger.warning("[spine_router] query embed failed: %s", exc)

    if qv is None:
        return [ScoredNode(node=node, match_score=0.0) for node in nodes]

    try:
        node_vecs = embed_texts([_slug_to_text(node.topic_slug) for node in nodes])
    except Exception as exc:
        logger.warning("[spine_router] node embed failed: %s", exc)
        node_vecs = [None] * len(nodes)

    scored: list[ScoredNode] = []
    for node, vec in zip(nodes, node_vecs):
        score = 0.0
        if vec is not None:
            try:
                raw = cosine_similarity(qv, vec)
                score = 0.0 if raw < 0.0 else (1.0 if raw > 1.0 else raw)
            except Exception:
                score = 0.0
        scored.append(ScoredNode(node=node, match_score=score))
    return scored


# ---------------------------------------------------------------------------
# Unmatched-query enqueue (Req 6.7)
# ---------------------------------------------------------------------------

def _enqueue_unmatched_query(query: str) -> bool:
    """Enqueue exactly one Backlog_Item for an unmatched query (Req 6.7).

    Routes a single pending Backlog_Item (keyed by a slug derived from the query)
    through the existing Topic_Frontier: it is merged via the pure
    ``topic_frontier.enqueue`` so a query that already has a non-done item is not
    duplicated, then the one newly accepted item (if any) is persisted via the
    ``backlog_store`` shell. Best-effort: any failure is logged and swallowed,
    returning False, so a feed/curriculum request is never blocked.

    Returns:
        True iff a new Backlog_Item was persisted for the query.
    """
    candidate = BacklogItem(
        topic=_query_to_slug(query),
        level=_DEFAULT_UNMATCHED_LEVEL,
        priority=_DEFAULT_UNMATCHED_PRIORITY,
        status="pending",
    )
    try:
        from app.services import backlog_store

        existing = backlog_store.load_pending()
        existing_topics = {item.topic for item in existing}
        merged = enqueue(existing, candidate)
        new_items = [item for item in merged if item.topic not in existing_topics]
        if not new_items:
            # Already present as a non-done item -> no duplicate (dedupe).
            return False
        backlog_store.upsert_items(new_items)
        logger.info(
            "[spine_router] enqueued unmatched query as '%s'", candidate.topic
        )
        return True
    except Exception as exc:
        logger.warning(
            "[spine_router] enqueue unmatched query '%s' failed: %s",
            candidate.topic, exc,
        )
        return False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def resolve_path(
    query: str,
    db=None,
    threshold: float = DEFAULT_SPINE_MATCH_THRESHOLD,
) -> RouteResult:
    """Resolve a learner query to an ordered Learning_Path of Spine_Nodes.

    Loads the non-pruned Curriculum_Spine (Pruned_Topics already excluded by
    ``load_spine`` -- Req 6.3), computes each Spine_Node's Match_Score (Req 6.1),
    and calls the pure :func:`~app.services.spine_router.route`. When the route
    flags an unmatched query, exactly one Backlog_Item is enqueued for it
    (Req 6.7); on a cyclic matched set (Req 6.8) or an invalid query (Req 6.9) the
    Topic_Frontier is left untouched. Best-effort: any failure degrades to a
    rejection-style result rather than raising.

    Args:
        query: The learner query.
        db: Optional Supabase client (injected in tests); defaults to the shared
            client used by ``load_spine``.
        threshold: The Spine_Match_Threshold (default 0.75).

    Returns:
        The pure :class:`RouteResult` describing the resolved path, any enqueue,
        and any rejection.

    Validates: Requirements 6.1, 6.7, 6.8, 6.9
    """
    # Reject an invalid query up front without touching the spine or the frontier
    # (Req 6.9). route() would reach the same verdict; doing it here avoids the
    # spine read entirely.
    invalid = validate_query(query)
    if invalid is not None:
        return RouteResult(
            path=(), enqueue_unmatched=False, rejection=invalid, cyclic_edge=None
        )

    try:
        nodes, edges = load_spine(db)
    except Exception as exc:
        logger.warning("[spine_router] load_spine failed: %s", exc)
        nodes, edges = [], []

    scored = _score_nodes(query, nodes)
    result = route(query, scored, edges, threshold)

    if result.enqueue_unmatched:
        # No Spine_Node matched: enqueue exactly one Backlog_Item (Req 6.7).
        _enqueue_unmatched_query(query)

    return result
