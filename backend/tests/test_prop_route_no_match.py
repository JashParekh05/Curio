"""Property-based test for Spine_Router no-match handling.

# Feature: content-retrieval-revamp, Property 24: No match flags exactly one enqueue and returns a closest path

*For any* valid query where every Spine_Node scores strictly below the threshold,
``route`` flags exactly one Backlog_Item enqueue for the unmatched query and
returns the closest available Spine_Node-based Learning_Path -- the single
highest-scoring node, ties broken by ascending slug -- without rejecting (Req 6.7).

Imports only the pure module under test (``spine_router`` plus the spine types it
reuses), so it runs offline with no external service.

Validates: Requirements 6.7
"""
from hypothesis import given, settings, strategies as st

from app.services.curriculum_spine import SpineEdge, SpineNode
from app.services.spine_router import ScoredNode, route

_SLUGS = ["a", "b", "c", "d", "e", "f"]


@st.composite
def _all_below_threshold(draw):
    """Build scored nodes whose every Match_Score is strictly below threshold.

    The threshold is drawn above 0 so the open interval [0, threshold) is
    non-empty; each node's score is drawn from it. Edges are drawn freely over
    the slug set (a single closest node never forms a cycle).
    """
    threshold = draw(st.floats(min_value=0.05, max_value=1.0))
    n = draw(st.integers(min_value=1, max_value=len(_SLUGS)))
    slugs = list(draw(st.permutations(_SLUGS)))[:n]

    scored = []
    for slug in slugs:
        score = draw(
            st.floats(
                min_value=0.0,
                max_value=threshold,
                exclude_max=True,
                allow_nan=False,
            )
        )
        scored.append(
            ScoredNode(
                node=SpineNode(topic_slug=slug, content_level="beginner", est_minutes=1),
                match_score=score,
            )
        )

    pairs = draw(st.lists(st.tuples(st.sampled_from(slugs), st.sampled_from(slugs)), max_size=6))
    edges = [SpineEdge(prerequisite=u, dependent=v) for u, v in pairs]
    return scored, edges, threshold


class TestRouteNoMatch:
    @settings(max_examples=100)
    @given(data=_all_below_threshold())
    def test_flags_exactly_one_enqueue(self, data):
        scored, edges, threshold = data
        result = route("an unmatched query", scored, edges, threshold)
        # No node met the threshold -> enqueue flagged, no rejection (Req 6.7).
        assert result.enqueue_unmatched is True
        assert result.rejection is None
        assert result.cyclic_edge is None

    @settings(max_examples=100)
    @given(data=_all_below_threshold())
    def test_returns_closest_available_node(self, data):
        scored, edges, threshold = data
        result = route("an unmatched query", scored, edges, threshold)

        # Closest available path is the single highest-scoring node, ties broken
        # by ascending slug (Req 6.7).
        assert len(result.path) == 1
        expected = min(
            scored, key=lambda sn: (-sn.match_score, sn.node.topic_slug)
        ).node
        assert result.path[0] == expected
