"""Property-based test for unfilled planned roles in the Segment_Mapper.

# Feature: deep-content-ingestion, Property 5: Unfilled planned roles are reported and never back-filled

For any pool of Learning_Atoms and any Planned_Arc, every Pedagogical_Role in
the arc that has no matching atom is reported in ``unfilled_roles`` and is never
back-filled with an atom of a different role: each Mapped_Segment always carries
its atom's own role.

Validates: Requirements 2.7
"""
from typing import get_args

from hypothesis import given, settings, strategies as st

from app.models.schemas import ArcRole, LearningAtom, PedagogicalRole, PlannedArc
from app.services.segment_mapper import partition_segments

_VALID_ROLES = list(get_args(PedagogicalRole))


@st.composite
def _planned_arc(draw):
    """A Planned_Arc with a distinct subset of roles, consecutive ordinals from 1."""
    roles = draw(st.lists(st.sampled_from(_VALID_ROLES), max_size=6, unique=True))
    arc_roles = [ArcRole(role=r, ordinal=i + 1) for i, r in enumerate(roles)]
    return PlannedArc(topic_slug="topic", concept_type="conceptual", roles=arc_roles)


@st.composite
def _atom_pool(draw):
    """A pool of Learning_Atoms with unique ids and roles drawn from the full
    set, so some planned roles are frequently left without any matching atom."""
    n = draw(st.integers(min_value=0, max_value=15))
    atoms: list[LearningAtom] = []
    for i in range(n):
        role = draw(st.sampled_from(_VALID_ROLES))
        start = draw(
            st.floats(min_value=0.0, max_value=300.0, allow_nan=False, allow_infinity=False)
        )
        atoms.append(
            LearningAtom(
                id=f"atom-{i}",
                topic_slug="topic",
                video_id="vid",
                source_url="https://example.com/v",
                role=role,
                concept=f"concept-{i}",
                prior_knowledge=[],
                start=start,
                end=start + 1.0,
            )
        )
    return atoms


class TestUnfilledRoles:
    @settings(max_examples=100)
    @given(arc=_planned_arc(), atoms=_atom_pool())
    def test_unfilled_roles_reported_and_never_back_filled(self, arc, atoms):
        result = partition_segments(atoms, arc)

        atom_roles = {a.role for a in atoms}

        # Distinct arc roles, preserving the arc's first-seen order.
        distinct_arc_roles: list[PedagogicalRole] = []
        seen: set[str] = set()
        for ar in arc.roles:
            if ar.role not in seen:
                seen.add(ar.role)
                distinct_arc_roles.append(ar.role)

        mapped_roles = {m.pedagogical_role for m in result.mapped}

        # --- Every arc role with no matching atom is reported as unfilled. ---
        for r in distinct_arc_roles:
            if r not in atom_roles:
                assert r in result.unfilled_roles

        # --- unfilled_roles contains only genuinely unfilled arc roles. ---
        for r in result.unfilled_roles:
            assert r in distinct_arc_roles
            assert r not in atom_roles
            # An unfilled role is never mapped (never back-filled).
            assert r not in mapped_roles

        # --- Never back-filled: each Mapped_Segment carries its atom's own role. ---
        for m in result.mapped:
            assert m.pedagogical_role == m.atom.role
