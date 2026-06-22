"""Property-based test for Spine_Router cyclic matched-set rejection.

# Feature: content-retrieval-revamp, Property 25: Cyclic matched set is rejected without an enqueue

*For any* valid query whose matched Spine_Nodes carry prerequisite edges that
contain a cycle, ``route`` rejects the resolution, records the cyclic
``(source, target)`` relationship, returns an empty path, and flags no
Topic_Frontier enqueue so the frontier is left unchanged (Req 6.8).

Imports only the pure module under test (``spine_router`` plus the spine types it
reuses), so it runs offline with no external service.

Validates: Requirements 6.8
"""
from hypothesis import given, settings, strategies as st

from app.services.curriculum_spine import SpineEdge, SpineNode, has_cycle
from app.services.spine_router import ScoredNode, route


@st.composite
def _matched_with_cycle(draw):
    """Build an all-matching scored set whose edges contain a directed cycle.

    All slugs score at or above the threshold (so every node is matched), and a
    closed ring over a 2..n prefix of the slugs guarantees a cycle within the
    matched set; extra forward edges may be added without removing it.
    """
    slugs = ["a", "b", "c", "d", "e"]
    threshold = draw(st.floats(min_value=0.0, max_value=1.0))

    scored = []
    for slug in slugs:
        score = draw(st.floats(min_value=threshold, max_value=1.0))
        scored.append(
            ScoredNode(
                node=SpineNode(topic_slug=slug, content_level="beginner", est_minutes=1),
                match_score=score,
            )
        )

    ring_len = draw(st.integers(min_value=2, max_value=len(slugs)))
    ring = slugs[:ring_len]
    edges = [
        SpineEdge(prerequisite=ring[i], dependent=ring[(i + 1) % ring_len])
        for i in range(ring_len)
    ]
    extra = draw(st.lists(st.tuples(st.sampled_from(slugs), st.sampled_from(slugs)), max_size=4))
    edges += [SpineEdge(prerequisite=u, dependent=v) for u, v in extra]
    return scored, edges, threshold


class TestRouteCyclicReject:
    @settings(max_examples=100)
    @given(data=_matched_with_cycle())
    def test_cyclic_matched_set_rejected_without_enqueue(self, data):
        scored, edges, threshold = data
        result = route("a cyclic query", scored, edges, threshold)

        assert result.rejection == "cyclic"
        # No enqueue: the Topic_Frontier is left unchanged (Req 6.8).
        assert result.enqueue_unmatched is False
        assert result.path == ()
        # The recorded relationship identifies a real edge that closes the cycle.
        assert result.cyclic_edge is not None
        src, dst = result.cyclic_edge
        assert any(e.prerequisite == src and e.dependent == dst for e in edges)
        assert has_cycle(
            [sn.node for sn in scored], list(edges)
        )
