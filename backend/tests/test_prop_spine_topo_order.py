"""Property-based test for Curriculum_Spine topological order.

# Feature: content-retrieval-revamp, Property 21: Topological order respects prerequisites with a deterministic tiebreak

*For any* random DAG, ``topo_order`` places every prerequisite ahead of every
dependent, includes each node exactly once, orders nodes with no prerequisite
relationship by ascending Topic slug, and is deterministic: identical input
yields identical output (Req 5.6, 5.10).

Imports only the pure module under test (``curriculum_spine``), so it runs
offline with no external service.

Validates: Requirements 5.6, 5.10
"""
from hypothesis import given, settings, strategies as st

from app.services.curriculum_spine import SpineEdge, SpineNode, topo_order

_SLUGS = ["a", "b", "c", "d", "e", "f", "g"]


@st.composite
def _dag(draw):
    """Build a random DAG over a subset of the fixed slug set.

    A random linear rank over the slugs guarantees acyclicity: every edge is
    oriented from a lower-ranked slug to a higher-ranked one.
    """
    n = draw(st.integers(min_value=1, max_value=len(_SLUGS)))
    slugs = draw(st.permutations(_SLUGS))
    slugs = list(slugs)[:n]
    rank = draw(st.permutations(slugs))
    rank_of = {slug: i for i, slug in enumerate(rank)}

    nodes = [
        SpineNode(topic_slug=s, content_level="beginner", est_minutes=1) for s in slugs
    ]
    possible = [(u, v) for u in slugs for v in slugs if rank_of[u] < rank_of[v]]
    chosen = draw(st.lists(st.sampled_from(possible), max_size=12)) if possible else []
    edges = [SpineEdge(prerequisite=u, dependent=v) for u, v in chosen]
    return nodes, edges


class TestTopoOrder:
    @settings(max_examples=100)
    @given(dag=_dag())
    def test_prerequisites_precede_dependents(self, dag):
        nodes, edges = dag
        order = topo_order(nodes, edges)
        position = {node.topic_slug: i for i, node in enumerate(order)}

        for edge in edges:
            # Both endpoints present (edges are built over the node set).
            assert position[edge.prerequisite] < position[edge.dependent]

    @settings(max_examples=100)
    @given(dag=_dag())
    def test_each_node_appears_exactly_once(self, dag):
        nodes, edges = dag
        order = topo_order(nodes, edges)

        slugs = [node.topic_slug for node in order]
        assert sorted(slugs) == sorted(n.topic_slug for n in nodes)
        assert len(slugs) == len(set(slugs))

    @settings(max_examples=100)
    @given(slugs=st.lists(st.sampled_from(_SLUGS), min_size=1, max_size=7, unique=True))
    def test_unrelated_nodes_order_by_ascending_slug(self, slugs):
        # No edges -> every node is unrelated -> pure ascending-slug order.
        nodes = [
            SpineNode(topic_slug=s, content_level="beginner", est_minutes=1)
            for s in slugs
        ]
        order = topo_order(nodes, [])
        assert [n.topic_slug for n in order] == sorted(slugs)

    @settings(max_examples=100)
    @given(dag=_dag())
    def test_deterministic(self, dag):
        nodes, edges = dag
        assert topo_order(nodes, edges) == topo_order(nodes, edges)
