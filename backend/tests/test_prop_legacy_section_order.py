"""Property-based test for legacy section mapping and order.

# Feature: content-retrieval-revamp, Property 3: Legacy sections map and order by section_index

Validates: Requirements 1.3

A Topic with only a Legacy_Section_Model yields a Canonical_Arc whose roles
equal ``[SECTION_ROLE_MAP[i] for i in sorted(indices)]`` -- each section_index
mapped via the Section_Role_Map and ordered by ascending section_index.
"""
from hypothesis import given, settings, strategies as st

from app.services.arc_unifier import SECTION_ROLE_MAP, unify_arc

_topic_slug = st.text(min_size=0, max_size=30)

# Non-empty subsets of {0,1,2,3}.
_section_indices = st.lists(
    st.integers(min_value=0, max_value=3), min_size=1, max_size=4, unique=True
)


class TestLegacySectionOrder:
    @settings(max_examples=100)
    @given(topic_slug=_topic_slug, section_indices=_section_indices)
    def test_arc_roles_equal_mapped_sorted_sections(self, topic_slug, section_indices):
        result = unify_arc(topic_slug, None, section_indices)
        assert result.arc is not None

        expected = [SECTION_ROLE_MAP[i] for i in sorted(section_indices)]
        actual = [ar.role for ar in result.arc.roles]
        assert actual == expected

    @settings(max_examples=100)
    @given(topic_slug=_topic_slug, section_indices=_section_indices)
    def test_duplicate_indices_collapse_and_stay_ordered(self, topic_slug, section_indices):
        # Re-supplying every index twice must not change the mapped, ordered arc.
        doubled = section_indices + section_indices
        result = unify_arc(topic_slug, None, doubled)
        assert result.arc is not None
        expected = [SECTION_ROLE_MAP[i] for i in sorted(set(section_indices))]
        actual = [ar.role for ar in result.arc.roles]
        assert actual == expected
