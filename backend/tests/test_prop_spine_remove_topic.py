"""Property-based test for Curriculum_Spine topic removal.

# Feature: content-retrieval-revamp, Property 22: Removing a topic drops it and its incident edges

*For any* spine and a Topic slug, ``remove_topic`` removes that node and every
edge for which the slug is the prerequisite or the dependent, while retaining
every other node and every non-incident edge unchanged (Req 5.9).

Imports only the pure module under test (``curriculum_spine``), so it runs
offline with no external service.

Validates: Requirements 5.9
"""
from hypothesis import given, settings, strategies as st

from app.services.curriculum_spine import SpineEdge, SpineNode, remove_topic

_slug = st.sampled_from(["a", "b", "c", "d", "e"])
_level = st.sampled_from(["beginner", "intermediate", "advanced"])


@st.composite
def _spine(draw):
    slugs = draw(
        st.lists(st.sampled_from(["a", "b", "c", "d", "e"]), min_size=1, max_size=5, unique=True)
    )
    nodes = [
        SpineNode(topic_slug=s, content_level=draw(_level), est_minutes=draw(st.integers(0, 60)))
        for s in slugs
    ]
    pairs = draw(st.lists(st.tuples(st.sampled_from(slugs), st.sampled_from(slugs)), max_size=8))
    # Dedupe edges so the retained-set assertions are exact.
    seen = set()
    edges = []
    for u, v in pairs:
        if (u, v) not in seen:
            seen.add((u, v))
            edges.append(SpineEdge(prerequisite=u, dependent=v))
    return nodes, edges


class TestRemoveTopic:
    @settings(max_examples=100)
    @given(spine=_spine(), target=_slug)
    def test_removes_node_and_incident_edges(self, spine, target):
        nodes, edges = spine
        new_nodes, new_edges = remove_topic(nodes, edges, target)

        # The target node is gone; every other node is retained.
        assert all(n.topic_slug != target for n in new_nodes)
        expected_nodes = [n for n in nodes if n.topic_slug != target]
        assert new_nodes == expected_nodes

        # Every incident edge is gone; every non-incident edge is retained.
        assert all(
            e.prerequisite != target and e.dependent != target for e in new_edges
        )
        expected_edges = [
            e for e in edges if e.prerequisite != target and e.dependent != target
        ]
        assert new_edges == expected_edges

    @settings(max_examples=100)
    @given(spine=_spine())
    def test_removing_absent_slug_is_a_no_op(self, spine):
        nodes, edges = spine
        new_nodes, new_edges = remove_topic(nodes, edges, "zzz-not-present")
        assert new_nodes == nodes
        assert new_edges == edges
