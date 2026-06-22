"""Property-based test for fillable planned-arc roles.

# Feature: content-hook-engagement, Property 8: Every fillable Planned_Arc role is filled

For any atom pool and Planned_Arc, every role that has at least one available
atom in the pool appears as the role of at least one assembled clip.

Validates: Requirements 3.1
"""
from typing import get_args

from hypothesis import given, settings, strategies as st

from app.agents.section_planner import build_planned_arc
from app.models.schemas import LearningAtom, PedagogicalRole
from app.services.arc_assembler import assemble

_VALID_ROLES = list(get_args(PedagogicalRole))


@st.composite
def _planned_arc_and_atoms(draw):
    """Generate a PlannedArc (a subset of roles with consecutive ordinals 1..n)
    plus a pool of LearningAtoms whose roles are drawn ONLY from those planned
    roles and whose prior_knowledge is empty (guaranteeing an acyclic
    prerequisite graph so no atom is dropped to a cycle)."""
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
    # Concepts are distinct per atom so selection never collapses two atoms of
    # the same (concept, role); each generated role therefore survives into the
    # assembled clips regardless of the deduplication step.
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
                concept=f"concept-{i}",  # distinct => no (concept, role) collisions
                prior_knowledge=[],  # empty => acyclic dependency graph
                start=start,
                end=start + 5.0,
            )
        )
    return planned_arc, atoms


class TestFillableRoles:
    @settings(max_examples=100)
    @given(data=_planned_arc_and_atoms())
    def test_every_fillable_role_is_filled(self, data):
        planned_arc, atoms = data

        clips, _defects = assemble(atoms, planned_arc)

        planned_roles = {ar.role for ar in planned_arc.roles}
        available_roles = {a.role for a in atoms}

        # Roles that are both planned AND have at least one available atom must
        # each appear as the pedagogical_role of at least one assembled clip.
        fillable_roles = planned_roles & available_roles

        realized_roles = {clip.pedagogical_role for clip in clips}

        missing = fillable_roles - realized_roles
        assert not missing, (
            f"fillable roles not present in assembled clips: {missing}; "
            f"fillable={fillable_roles} realized={realized_roles}"
        )
