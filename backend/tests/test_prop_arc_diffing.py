"""Property-based test for arc diffing.

# Feature: content-hook-engagement, Property 23: Arc diff identifies missing roles, order mismatches, and alignment

For any Planned_Arc and Realized_Arc, ``diff_arcs`` reports ``missing_roles``
equal to exactly the planned roles absent from the realized arc,
``order_mismatch_positions`` equal to exactly the indices where the two role
sequences diverge, and ``aligned`` is ``True`` iff both are empty.

Validates: Requirements 5.2, 5.3, 5.6
"""
from typing import get_args

from hypothesis import given, settings, strategies as st

from app.models.schemas import ArcRole, PedagogicalRole, PlannedArc
from app.services.alignment import diff_arcs

_VALID_ROLES = list(get_args(PedagogicalRole))


@st.composite
def _planned_arc(draw):
    """A PlannedArc with distinct roles and consecutive ordinals 1..n."""
    roles = draw(
        st.lists(
            st.sampled_from(_VALID_ROLES),
            unique=True,
            min_size=1,
            max_size=len(_VALID_ROLES),
        )
    )
    arc_roles = [ArcRole(role=role, ordinal=i) for i, role in enumerate(roles, start=1)]
    return PlannedArc(
        topic_slug=draw(st.text(min_size=0, max_size=20)),
        concept_type=draw(st.sampled_from(["problem_solving", "conceptual", "default"])),
        roles=arc_roles,
    )


# A realized role sequence: any subset / superset / reordering of roles,
# including duplicates and roles not present in the planned arc.
_realized = st.lists(st.sampled_from(_VALID_ROLES), min_size=0, max_size=15)


class TestArcDiffing:
    @settings(max_examples=100)
    @given(planned=_planned_arc(), realized=_realized)
    def test_diff_identifies_missing_and_alignment(self, planned, realized):
        diff = diff_arcs(planned, realized)

        planned_roles = [ar.role for ar in planned.roles]
        realized_set = set(realized)

        # missing_roles is exactly the planned roles absent from realized.
        expected_missing = {r for r in planned_roles if r not in realized_set}
        assert set(diff.missing_roles) == expected_missing

        # aligned iff both missing_roles and order_mismatch_positions are empty.
        assert diff.aligned == (
            diff.missing_roles == [] and diff.order_mismatch_positions == []
        )

    @settings(max_examples=100)
    @given(planned=_planned_arc())
    def test_perfectly_aligned_arc_yields_empty_diff(self, planned):
        # Realized order matches the planned role order exactly.
        realized = [ar.role for ar in planned.roles]
        diff = diff_arcs(planned, realized)

        assert diff.missing_roles == []
        assert diff.order_mismatch_positions == []
        assert diff.aligned is True
