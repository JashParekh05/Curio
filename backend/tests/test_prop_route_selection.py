"""Property-based test for Spine_Router selection and ordering.

# Feature: content-retrieval-revamp, Property 23: Routing selects threshold-matching nodes in prerequisite order, bounded and distinct

*For any* valid query, set of non-pruned scored nodes straddling the threshold,
and acyclic matched prerequisite edges, ``route`` returns a Learning_Path that is
exactly the >= threshold nodes (distinct, capped at 50, and 1..50 whenever any
node matched), ordered so every prerequisite precedes its dependent with position
ties broken by ascending stable slug, and is deterministic: identical inputs
yield identical paths (Req 6.1, 6.2, 6.4, 6.5, 6.6, 6.10, 6.11).

Imports only the pure module under test (``spine_router`` plus the spine types it
reuses), so it runs offline with no external service.

Validates: Requirements 6.1, 6.2, 6.4, 6.5, 6.6, 6.10, 6.11
"""
from hypothesis import given, settings, strategies as st

from app.services.curriculum_spine import SpineEdge, SpineNode, topo_order
from app.services.spine_router import (
    MAX_PATH_NODES,
    ScoredNode,
    route,
)

_SLUGS = ["a", "b", "c", "d", "e", "f", "g"]


@st.composite
def _scored_acyclic(draw):
    """Build scored non-pruned nodes plus a guaranteed-acyclic edge set.

    A random linear rank over the chosen slugs guarantees acyclicity: every edge
    runs from a lower-ranked slug to a higher-ranked one. Each node draws a
    Match_Score in [0.0, 1.0]; the threshold is drawn separately so scores
    straddle it.
    """
    n = draw(st.integers(min_value=1, max_value=len(_SLUGS)))
    slugs = list(draw(st.permutations(_SLUGS)))[:n]
    rank = draw(st.permutations(slugs))
    rank_of = {slug: i for i, slug in enumerate(rank)}

    scored = []
    for slug in slugs:
        score = draw(st.floats(min_value=0.0, max_value=1.0))
        scored.append(
            ScoredNode(
                node=SpineNode(topic_slug=slug, content_level="beginner", est_minutes=1),
                match_score=score,
            )
        )

    possible = [(u, v) for u in slugs for v in slugs if rank_of[u] < rank_of[v]]
    chosen = draw(st.lists(st.sampled_from(possible), max_size=12)) if possible else []
    edges = [SpineEdge(prerequisite=u, dependent=v) for u, v in chosen]

    threshold = draw(st.floats(min_value=0.0, max_value=1.0))
    return scored, edges, threshold


class TestRouteSelection:
    @settings(max_examples=100)
    @given(data=_scored_acyclic())
    def test_path_is_exactly_threshold_matching_nodes(self, data):
        scored, edges, threshold = data
        result = route("learn something", scored, edges, threshold)
        assert result.rejection is None

        expected = {sn.node.topic_slug for sn in scored if sn.match_score >= threshold}
        path_slugs = {n.topic_slug for n in result.path}
        if expected:
            # At least one match -> the path is exactly the matched nodes,
            # enqueue-free (Req 6.6).
            assert path_slugs == expected
            assert result.enqueue_unmatched is False
        else:
            # No match -> enqueue flagged and a closest single-node path
            # is returned instead (Req 6.7).
            assert result.enqueue_unmatched is True
            assert path_slugs <= {sn.node.topic_slug for sn in scored}

    @settings(max_examples=100)
    @given(data=_scored_acyclic())
    def test_path_is_distinct_and_bounded(self, data):
        scored, edges, threshold = data
        result = route("learn something", scored, edges, threshold)
        slugs = [n.topic_slug for n in result.path]
        # Distinct (Req 6.4).
        assert len(slugs) == len(set(slugs))
        # Capped at 50 (Req 6.4).
        assert len(slugs) <= MAX_PATH_NODES

    @settings(max_examples=100)
    @given(data=_scored_acyclic())
    def test_prerequisites_precede_dependents(self, data):
        scored, edges, threshold = data
        result = route("learn something", scored, edges, threshold)
        position = {n.topic_slug: i for i, n in enumerate(result.path)}
        matched = set(position)
        for edge in edges:
            if edge.prerequisite in matched and edge.dependent in matched:
                assert position[edge.prerequisite] < position[edge.dependent]

    @settings(max_examples=100)
    @given(data=_scored_acyclic())
    def test_order_matches_topo_with_slug_tiebreak(self, data):
        scored, edges, threshold = data
        result = route("learn something", scored, edges, threshold)

        matched_slugs = {
            sn.node.topic_slug for sn in scored if sn.match_score >= threshold
        }
        if not matched_slugs:
            # No-match case is governed by the no-match property test, not here.
            return
        matched_nodes = [
            sn.node for sn in scored if sn.node.topic_slug in matched_slugs
        ]
        matched_edges = [
            e
            for e in edges
            if e.prerequisite in matched_slugs and e.dependent in matched_slugs
        ]
        expected = topo_order(matched_nodes, matched_edges)[:MAX_PATH_NODES]
        # Position ties break by ascending stable slug (Req 6.2, 6.5).
        assert list(result.path) == expected

    @settings(max_examples=100)
    @given(data=_scored_acyclic())
    def test_deterministic(self, data):
        scored, edges, threshold = data
        first = route("same query", scored, edges, threshold)
        second = route("same query", scored, edges, threshold)
        assert first == second
