"""Property-based test for the Segment_Mapper mapping partition.

# Feature: deep-content-ingestion, Property 4: Mapping partition is total and assigns at most one atom per role

For any pool of Learning_Atoms and any Planned_Arc, ``partition_segments``:
  - is TOTAL over the input — every input atom appears exactly once across the
    mapped and unmapped outputs (no atom lost or duplicated);
  - records each Mapped_Segment carrying exactly the one Pedagogical_Role the
    atom's own role corresponds to, which must be present in the arc;
  - records at most one Mapped_Segment per Pedagogical_Role;
  - sends surplus atoms competing for an already-filled role, and atoms whose
    role is absent from the arc, to the unmapped set with their role unchanged,
    selecting the winner with the deterministic (earliest start, then atom id)
    total order.

Validates: Requirements 2.1, 2.4, 2.5, 2.6
"""
from typing import get_args

from hypothesis import given, settings, strategies as st

from app.models.schemas import ArcRole, LearningAtom, PedagogicalRole, PlannedArc
from app.services.segment_mapper import partition_segments

_VALID_ROLES = list(get_args(PedagogicalRole))


@st.composite
def _planned_arc(draw):
    """A Planned_Arc with a distinct subset of roles, consecutive ordinals from 1."""
    roles = draw(st.lists(st.sampled_from(_VALID_ROLES), max_size=5, unique=True))
    arc_roles = [ArcRole(role=r, ordinal=i + 1) for i, r in enumerate(roles)]
    return PlannedArc(topic_slug="topic", concept_type="conceptual", roles=arc_roles)


@st.composite
def _atom_pool(draw):
    """A pool of Learning_Atoms with UNIQUE ids so the partition is countable.

    Roles are drawn from the full valid set so the pool contains both atoms
    eligible for the (small) arc and ineligible atoms, and frequently several
    atoms competing for the same role — exercising the surplus path.
    """
    n = draw(st.integers(min_value=0, max_value=20))
    atoms: list[LearningAtom] = []
    for i in range(n):
        role = draw(st.sampled_from(_VALID_ROLES))
        start = draw(
            st.floats(min_value=0.0, max_value=500.0, allow_nan=False, allow_infinity=False)
        )
        atoms.append(
            LearningAtom(
                id=f"atom-{i}",  # unique within the pool
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


class TestMappingPartition:
    @settings(max_examples=100)
    @given(arc=_planned_arc(), atoms=_atom_pool())
    def test_partition_is_total_and_at_most_one_per_role(self, arc, atoms):
        result = partition_segments(atoms, arc)

        arc_roles = {ar.role for ar in arc.roles}
        mapped_ids = [m.atom.id for m in result.mapped]
        unmapped_ids = [a.id for a in result.unmapped]

        # --- TOTAL: every input atom appears exactly once (no loss / no dup). ---
        input_ids = [a.id for a in atoms]
        assert sorted(mapped_ids + unmapped_ids) == sorted(input_ids)
        assert len(set(mapped_ids) & set(unmapped_ids)) == 0

        # --- Each Mapped_Segment carries the atom's OWN role, present in arc. ---
        for m in result.mapped:
            assert m.pedagogical_role == m.atom.role
            assert m.pedagogical_role in arc_roles
            # role_ordinal matches the arc's ordinal for that role.
            assert any(
                ar.role == m.pedagogical_role and ar.ordinal == m.role_ordinal
                for ar in arc.roles
            )

        # --- At most one Mapped_Segment per Pedagogical_Role. ---
        mapped_roles = [m.pedagogical_role for m in result.mapped]
        assert len(mapped_roles) == len(set(mapped_roles))

        # --- Atoms whose role is absent from the arc are unmapped (role unchanged). ---
        for a in atoms:
            if a.role not in arc_roles:
                assert a.id in unmapped_ids

        # --- For each filled role: deterministic winner + surplus go to unmapped. ---
        for ar in arc.roles:
            role_atoms = [a for a in atoms if a.role == ar.role]
            if not role_atoms:
                continue
            mapped_for_role = [m for m in result.mapped if m.pedagogical_role == ar.role]
            assert len(mapped_for_role) == 1
            winner = mapped_for_role[0].atom
            # Deterministic total order: earliest start, then atom id.
            expected = sorted(role_atoms, key=lambda a: (a.start, a.id))[0]
            assert winner.id == expected.id
            # Every surplus atom for this role is in the unmapped set.
            surplus_ids = {a.id for a in role_atoms if a.id != winner.id}
            assert surplus_ids <= set(unmapped_ids)

        # --- Unmapped atoms keep their original role (never reassigned). ---
        by_id = {a.id: a for a in atoms}
        for u in result.unmapped:
            assert u.role == by_id[u.id].role
