"""Property-based test for Curriculum_Spine persistence round-trip.

# Feature: content-retrieval-revamp, Property 18: Persistence round-trip is identity

*For any* spine, serializing the nodes and edges and then deserializing the
resulting rows reproduces the same node slugs, Content_Levels, and edge
endpoints -- the round-trip is the identity over the recorded fields (Req 5.1).

Imports only the pure module under test (``curriculum_spine``), so it runs
offline with no external service. The store (``curriculum_spine_store``) relies
on exactly this pure round-trip to reload the spine identically after restart.

Validates: Requirements 5.1
"""
from hypothesis import given, settings, strategies as st

from app.services.curriculum_spine import (
    SpineEdge,
    SpineNode,
    deserialize_edges,
    deserialize_nodes,
    serialize_edges,
    serialize_nodes,
)

_slug = st.sampled_from(["a", "b", "c", "d", "e"])
_level = st.sampled_from(["beginner", "intermediate", "advanced"])


@st.composite
def _spine(draw):
    slugs = draw(
        st.lists(st.sampled_from(["a", "b", "c", "d", "e"]), min_size=0, max_size=5, unique=True)
    )
    nodes = [
        SpineNode(
            topic_slug=s,
            content_level=draw(_level),
            est_minutes=draw(st.integers(min_value=0, max_value=240)),
        )
        for s in slugs
    ]
    pairs = draw(st.lists(st.tuples(_slug, _slug), max_size=8))
    edges = [SpineEdge(prerequisite=u, dependent=v) for u, v in pairs]
    return nodes, edges


class TestRoundTrip:
    @settings(max_examples=100)
    @given(spine=_spine())
    def test_node_round_trip_is_identity(self, spine):
        nodes, _edges = spine
        reloaded = deserialize_nodes(serialize_nodes(nodes))
        assert reloaded == nodes
        # Explicitly: slugs, content levels, and est_minutes are all preserved.
        assert [(n.topic_slug, n.content_level, n.est_minutes) for n in reloaded] == [
            (n.topic_slug, n.content_level, n.est_minutes) for n in nodes
        ]

    @settings(max_examples=100)
    @given(spine=_spine())
    def test_edge_round_trip_is_identity(self, spine):
        _nodes, edges = spine
        reloaded = deserialize_edges(serialize_edges(edges))
        assert reloaded == edges
        assert [(e.prerequisite, e.dependent) for e in reloaded] == [
            (e.prerequisite, e.dependent) for e in edges
        ]
