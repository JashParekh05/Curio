"""Property-based test for planned-arc role ordering in assembly.

# Feature: content-hook-engagement, Property 9: Assembly respects planned-arc role order

For any selected atoms and Planned_Arc whose dependencies are acyclic, the
sequence of role ordinals across the ordered atoms is monotonically
non-decreasing with respect to the Planned_Arc order.

Validates: Requirements 3.2
"""
from typing import get_args

from hypothesis import given, settings, strategies as st

from app.agents.section_planner import build_planned_arc
from app.models.schemas import LearningAtom, PedagogicalRole
from app.services.arc_assembler import topological_order

_VALID_ROLES = list(get_args(PedagogicalRole))


@st.composite
def _planned_arc_and_atoms(draw):
    """Generate a PlannedArc (a subset of roles with consecutive ordinals 1..n)
    plus a pool of LearningAtoms whose roles are drawn ONLY from those planned
    roles and whose prior_knowledge is empty (guaranteeing an acyclic
    prerequisite graph)."""
    # --- Planned arc: a non-empty subset of distinct roles, in some order. ---
    n_roles = draw(st.integers(min_value=1, max_value=len(_VALID_ROLES)))
    template = draw(
        st.lists(
            st.sampled_from(_VALID_ROLES),
            min_size=n_roles,
            max_size=n_roles,
            unique=True,
        )
    )
    planned_arc = build_planned_arc(
        topic_slug="topic",
        concept_type="default",
        template=template,
    )

    # --- Atoms: roles drawn only from the planned roles; no prerequisites. ---
    n_atoms = draw(st.integers(min_value=0, max_value=25))
    atoms: list[LearningAtom] = []
    for i in range(n_atoms):
        role = draw(st.sampled_from(template))
        start = draw(
            st.floats(
                min_value=0.0, max_value=500.0, allow_nan=False, allow_infinity=False
            )
        )
        atoms.append(
            LearningAtom(
                id=f"atom-{i}",
                topic_slug="topic",
                video_id="vid",
                source_url="https://example.com/v",
                role=role,
                concept=f"concept-{i}",  # distinct so no (concept, role) collisions
                prior_knowledge=[],  # empty => acyclic dependency graph
                start=start,
                end=start + 5.0,
            )
        )
    return planned_arc, atoms


class TestPlannedArcRoleOrder:
    @settings(max_examples=100)
    @given(data=_planned_arc_and_atoms())
    def test_role_ordinals_are_non_decreasing(self, data):
        planned_arc, atoms = data

        ordered, _defects = topological_order(atoms, planned_arc)

        # No atom should be dropped: ordering retains every input atom.
        assert len(ordered) == len(atoms)

        # Map each ordered atom to its planned ordinal.
        role_ordinal = {ar.role: ar.ordinal for ar in planned_arc.roles}
        ordinals = [role_ordinal[a.role] for a in ordered]

        # The ordinal sequence must be monotonically non-decreasing.
        assert all(
            ordinals[i] <= ordinals[i + 1] for i in range(len(ordinals) - 1)
        ), f"role ordinals not non-decreasing: {ordinals}"
