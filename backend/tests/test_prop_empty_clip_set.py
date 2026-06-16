"""Property-based test for the empty assembled clip set.

# Feature: content-hook-engagement, Property 26: Empty clip set yields all roles missing and not aligned

For any Planned_Arc with an empty assembled clip set, diff_arcs/check_and_repair
reports missing_roles equal to all planned roles and aligned=False.

Validates: Requirements 5.8
"""
from typing import get_args

from hypothesis import given, settings, strategies as st

from app.agents.section_planner import build_planned_arc
from app.models.schemas import PedagogicalRole
from app.services.alignment import check_and_repair, diff_arcs

_VALID_ROLES = list(get_args(PedagogicalRole))


@st.composite
def _planned_arc(draw):
    """Generate a PlannedArc with a random NON-EMPTY set of distinct roles,
    tagged with consecutive ordinals 1..n in template order."""
    n_roles = draw(st.integers(min_value=1, max_value=len(_VALID_ROLES)))
    template = draw(
        st.lists(
            st.sampled_from(_VALID_ROLES),
            min_size=n_roles,
            max_size=n_roles,
            unique=True,
        )
    )
    return build_planned_arc(
        topic_slug="topic",
        concept_type="default",
        template=template,
    )


class TestEmptyClipSet:
    @settings(max_examples=100)
    @given(planned=_planned_arc())
    def test_diff_arcs_empty_realized(self, planned):
        all_roles = [ar.role for ar in planned.roles]

        diff = diff_arcs(planned, [])

        # Every planned role is reported missing, and the arc is not aligned.
        assert set(diff.missing_roles) == set(all_roles)
        assert diff.aligned is False

    @settings(max_examples=100)
    @given(planned=_planned_arc())
    def test_check_and_repair_empty_clips(self, planned):
        all_roles = [ar.role for ar in planned.roles]

        result = check_and_repair([], planned, atom_pool=[])

        # No clips and no atoms to fill from => not aligned, all roles missing.
        assert result.aligned is False
        assert set(result.diff.missing_roles) == set(all_roles)
        assert result.diff.aligned is False
