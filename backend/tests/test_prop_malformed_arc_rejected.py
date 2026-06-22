"""Property-based test for malformed-structure rejection.

# Feature: content-retrieval-revamp, Property 8: Malformed structure is rejected without an arc

Validates: Requirements 1.9, 1.10

A Role_Arc_Model with non-consecutive, gapped, or duplicate ordinals produces no
arc and records ``non_consecutive_ordinals`` (Req 1.9). A Legacy_Section_Model
with any section_index outside 0..3 produces no arc and records
``section_index_out_of_range`` (Req 1.10).
"""
from hypothesis import assume, given, settings, strategies as st

from app.models.schemas import ArcRole, PedagogicalRole
from app.services.arc_unifier import (
    REJECTION_NON_CONSECUTIVE,
    REJECTION_SECTION_OUT_OF_RANGE,
    unify_arc,
)

_PEDAGOGICAL_ROLES: list[PedagogicalRole] = [
    "problem_statement", "meaning", "visualization", "approach",
    "worked_example", "edge_cases",
    "definition", "motivation", "mechanism", "example", "common_misconception",
]

_topic_slug = st.text(min_size=0, max_size=30)


@st.composite
def _malformed_role_model(draw):
    """A non-empty Role_Arc_Model whose ordinals are NOT 1..n (gap/dupe/shift)."""
    n = draw(st.integers(min_value=1, max_value=8))
    roles = draw(st.lists(st.sampled_from(_PEDAGOGICAL_ROLES), min_size=n, max_size=n))
    # Ordinals drawn from a wider band so they can gap, duplicate, or not start at 1.
    ordinals = draw(
        st.lists(st.integers(min_value=-2, max_value=12), min_size=n, max_size=n)
    )
    # Reject the (rare) case where the random ordinals happen to be a valid 1..n set.
    assume(sorted(ordinals) != list(range(1, n + 1)))
    return [ArcRole(role=role, ordinal=o) for role, o in zip(roles, ordinals)]


@st.composite
def _sections_with_out_of_range(draw):
    """Section indices including at least one value outside 0..3."""
    in_range = draw(st.lists(st.integers(min_value=0, max_value=3), max_size=4))
    out = draw(
        st.lists(
            st.integers(min_value=-50, max_value=50).filter(lambda i: i < 0 or i > 3),
            min_size=1, max_size=4,
        )
    )
    combined = in_range + out
    draw(st.randoms()).shuffle(combined)
    return combined


class TestMalformedArcRejected:
    @settings(max_examples=100)
    @given(topic_slug=_topic_slug, role_model=_malformed_role_model())
    def test_malformed_role_model_rejected(self, topic_slug, role_model):
        result = unify_arc(topic_slug, role_model, None)
        assert result.arc is None
        assert result.rejection == REJECTION_NON_CONSECUTIVE
        assert result.structureless is False

    @settings(max_examples=100)
    @given(topic_slug=_topic_slug, section_indices=_sections_with_out_of_range())
    def test_out_of_range_sections_rejected(self, topic_slug, section_indices):
        result = unify_arc(topic_slug, None, section_indices)
        assert result.arc is None
        assert result.rejection == REJECTION_SECTION_OUT_OF_RANGE
        assert result.structureless is False

    @settings(max_examples=100)
    @given(topic_slug=_topic_slug, role_model=_malformed_role_model())
    def test_malformed_role_model_takes_precedence_over_legacy(self, topic_slug, role_model):
        # Role model precedence: a malformed role model rejects even when a
        # valid legacy model is also present (Req 1.9 precedence).
        result = unify_arc(topic_slug, role_model, [0, 1, 2, 3])
        assert result.arc is None
        assert result.rejection == REJECTION_NON_CONSECUTIVE
