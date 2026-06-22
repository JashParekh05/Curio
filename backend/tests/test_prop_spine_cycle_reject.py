"""Property-based test for Curriculum_Spine cycle rejection.

# Feature: content-retrieval-revamp, Property 19: Cycle-creating edges are rejected; the graph stays acyclic

*For any* acyclic spine and a candidate edge, ``add_edge`` adds the edge only
when the result stays acyclic; a cycle-creating candidate is rejected with the
nodes and edges left unchanged and the rejected ``(source, target)`` recorded,
and any sequence of accepted edges keeps the graph acyclic (Req 5.3, 5.4).

Imports only the pure module under test (``curriculum_spine``), so it runs
offline with no external service.

Validates: Requirements 5.3, 5.4
"""
from hypothesis import given, settings, strategies as st

from app.services.curriculum_spine import (
    SpineEdge,
    SpineNode,
    add_edge,
    has_cycle,
)

_slug = st.sampled_from(["a", "b", "c", "d", "e", "f"])


@st.composite
def _acyclic_spine(draw):
    """Build a guaranteed-acyclic spine over a fixed slug set.

    Slugs are assigned a random linear rank; every candidate edge is oriented
    from the lower rank to the higher rank, so the constructed edge set is a DAG.
    """
    slugs = ["a", "b", "c", "d", "e", "f"]
    rank = draw(st.permutations(slugs))
    rank_of = {slug: i for i, slug in enumerate(rank)}
    nodes = [
        SpineNode(topic_slug=s, content_level="beginner", est_minutes=5) for s in slugs
    ]

    candidate_pairs = [
        (u, v) for u in slugs for v in slugs if rank_of[u] < rank_of[v]
    ]
    chosen = draw(st.lists(st.sampled_from(candidate_pairs), max_size=8))
    edges = [SpineEdge(prerequisite=u, dependent=v) for u, v in chosen]
    return nodes, edges


class TestCycleRejection:
    @settings(max_examples=100)
    @given(spine=_acyclic_spine(), src=_slug, dst=_slug)
    def test_cycle_creating_edge_rejected_unchanged(self, spine, src, dst):
        nodes, edges = spine
        candidate = SpineEdge(prerequisite=src, dependent=dst)

        result = add_edge(nodes, edges, candidate)

        creates_cycle = has_cycle(nodes, [*edges, candidate])
        already_present = any(
            e.prerequisite == src and e.dependent == dst for e in edges
        )

        if creates_cycle and not already_present:
            # Rejected: graph unchanged and the rejected (source, target) recorded.
            assert result.rejected_edge == (src, dst)
            assert result.nodes == tuple(nodes)
            assert result.edges == tuple(edges)
        else:
            assert result.rejected_edge is None
            # The accepted graph is acyclic.
            assert not has_cycle(list(result.nodes), list(result.edges))

    @settings(max_examples=100)
    @given(spine=_acyclic_spine(), pairs=st.lists(st.tuples(_slug, _slug), max_size=10))
    def test_accepted_sequence_stays_acyclic(self, spine, pairs):
        nodes, edges = spine
        edges = list(edges)
        # A freshly built acyclic spine is acyclic to begin with.
        assert not has_cycle(nodes, edges)
        for src, dst in pairs:
            result = add_edge(nodes, edges, SpineEdge(prerequisite=src, dependent=dst))
            edges = list(result.edges)
            # After every add_edge -- accepted or rejected -- the graph is acyclic.
            assert not has_cycle(nodes, edges)
