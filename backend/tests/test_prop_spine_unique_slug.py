"""Property-based test for Curriculum_Spine node slug uniqueness.

# Feature: content-retrieval-revamp, Property 20: Node slugs are unique

*For any* sequence of node registrations, registering a node whose Topic slug is
already present leaves the existing nodes unchanged and records the rejected
slug, so the spine holds at most one Spine_Node per Topic slug (Req 5.5).

Imports only the pure module under test (``curriculum_spine``), so it runs
offline with no external service.

Validates: Requirements 5.5
"""
from hypothesis import given, settings, strategies as st

from app.services.curriculum_spine import SpineNode, add_node

_slug = st.sampled_from(["a", "b", "c", "d"])
_level = st.sampled_from(["beginner", "intermediate", "advanced"])


@st.composite
def _candidate(draw):
    return SpineNode(
        topic_slug=draw(_slug),
        content_level=draw(_level),
        est_minutes=draw(st.integers(min_value=0, max_value=120)),
    )


class TestUniqueSlug:
    @settings(max_examples=100)
    @given(candidates=st.lists(_candidate(), max_size=12))
    def test_registration_keeps_slugs_unique(self, candidates):
        nodes: list[SpineNode] = []
        for candidate in candidates:
            existing_slugs = {n.topic_slug for n in nodes}
            result = add_node(nodes, candidate)

            if candidate.topic_slug in existing_slugs:
                # Duplicate rejected: nodes unchanged, rejected slug recorded.
                assert result.rejected_slug == candidate.topic_slug
                assert result.nodes == tuple(nodes)
            else:
                assert result.rejected_slug is None
                assert candidate in result.nodes

            nodes = list(result.nodes)
            # No slug ever repeats in the registered node set.
            slugs = [n.topic_slug for n in nodes]
            assert len(slugs) == len(set(slugs))

    @settings(max_examples=100)
    @given(node=_candidate())
    def test_duplicate_leaves_first_registration_intact(self, node):
        first = add_node([], node)
        assert first.rejected_slug is None

        duplicate = SpineNode(
            topic_slug=node.topic_slug,
            content_level="advanced",
            est_minutes=node.est_minutes + 99,
        )
        second = add_node(list(first.nodes), duplicate)

        assert second.rejected_slug == node.topic_slug
        # The originally registered node is retained, not overwritten.
        assert second.nodes == first.nodes
