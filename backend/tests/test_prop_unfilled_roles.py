"""Property-based test for unfilled planned-arc roles.

# Feature: content-hook-engagement, Property 13: Absent roles are left unfilled, flagged, and never substituted

For any Planned_Arc containing a role with no available atom in the pool, the
assembly output records an ``unfilled_role`` defect for that role and no
produced clip is assigned that role using atoms of a different role.

Validates: Requirements 3.8
"""
from typing import get_args

from hypothesis import given, settings, strategies as st

from app.agents.section_planner import build_planned_arc
from app.models.schemas import LearningAtom, PedagogicalRole
from app.services.arc_assembler import assemble

_VALID_ROLES = list(get_args(PedagogicalRole))


@st.composite
def _planned_arc_and_partial_pool(draw):
    """Generate a PlannedArc with several distinct roles plus an atom pool that
    deliberately OMITS atoms for at least one planned role.

    The atom pool's roles are drawn ONLY from a *strict* subset of the planned
    roles, so at least one planned role is guaranteed to have no atom. Atoms
    carry empty prior_knowledge so the prerequisite graph stays acyclic and the
    assembly never drops/reorders for prerequisite reasons.
    """
    # --- Planned arc: >= 2 distinct roles so a strict subset can omit one. ---
    n_roles = draw(st.integers(min_value=2, max_value=len(_VALID_ROLES)))
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

    # --- Covered roles: a STRICT subset of planned roles (size 0..n-1). ---
    # Drawing size up to n_roles - 1 guarantees at least one omitted role.
    n_covered = draw(st.integers(min_value=0, max_value=n_roles - 1))
    covered_roles = draw(
        st.lists(
            st.sampled_from(template),
            min_size=n_covered,
            max_size=n_covered,
            unique=True,
        )
    )

    # --- Atoms: roles drawn only from the covered subset; no prerequisites. ---
    if covered_roles:
        n_atoms = draw(st.integers(min_value=0, max_value=20))
    else:
        # No covered roles => no atoms can be generated.
        n_atoms = 0

    atoms: list[LearningAtom] = []
    for i in range(n_atoms):
        role = draw(st.sampled_from(covered_roles))
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


class TestUnfilledRoles:
    @settings(max_examples=100)
    @given(data=_planned_arc_and_partial_pool())
    def test_absent_roles_flagged_and_never_substituted(self, data):
        planned_arc, atoms = data

        clips, defects = assemble(atoms, planned_arc)

        # Planned roles that have NO atom in the pool are "unfilled".
        roles_in_pool = {a.role for a in atoms}
        planned_roles = {ar.role for ar in planned_arc.roles}
        unfilled_roles = {r for r in planned_roles if r not in roles_in_pool}

        # Sanity: the generator guarantees at least one unfilled role.
        assert unfilled_roles, "generator should always omit at least one planned role"

        # Roles that the assembly flagged as unfilled.
        flagged_roles = {
            d.role for d in defects if d.defect_type == "unfilled_role"
        }

        # Roles actually present on produced clips.
        clip_roles = {c.pedagogical_role for c in clips}

        for role in unfilled_roles:
            # (a) An unfilled_role defect is recorded for that role.
            assert role in flagged_roles, (
                f"expected unfilled_role defect for {role!r}; "
                f"flagged={flagged_roles}"
            )
            # (b) No produced clip is assigned that role (never substituted).
            assert role not in clip_roles, (
                f"role {role!r} has no atom yet appears on a clip "
                f"(unexpected substitution); clip_roles={clip_roles}"
            )
