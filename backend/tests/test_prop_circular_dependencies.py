"""Property-based test for circular prerequisite dependencies.

# Feature: content-hook-engagement, Property 14: Circular dependencies are ordered deterministically and flagged

For any atom set whose prerequisite dependencies contain a cycle,
``topological_order`` returns all atoms (none dropped), orders the cycle
members by their deterministic selection order (start ASC, id ASC), and records
a ``circular_dependency`` Coherence_Defect.

Validates: Requirements 3.9
"""
from hypothesis import given, settings, strategies as st

from app.services.arc_assembler import topological_order
from app.models.schemas import ArcRole, LearningAtom, PlannedArc


# All cycle atoms share the SAME role so the role-ordinal sort key does not
# pre-resolve ordering — the deterministic (start ASC, id ASC) tiebreaker must
# be what orders them.
_CYCLE_ROLE = "definition"


@st.composite
def _cycle_atoms(draw):
    """Construct a set of atoms whose prerequisite graph forms a single cycle.

    For a cycle of size N, atom i covers concept ``c{i}`` and lists ``c{i+1}``
    (mod N) in its prior_knowledge. Thus c0 -> c1 -> ... -> c{N-1} -> c0, a
    closed dependency loop that Kahn's algorithm can never drain.

    All atoms share ``_CYCLE_ROLE`` so role-ordinal cannot order them.
    Distinct ids and starts are drawn so the (start ASC, id ASC) ordering is
    well-defined and observable.
    """
    n = draw(st.integers(min_value=2, max_value=6))

    # Distinct ids (so the id tiebreaker is unambiguous).
    ids = draw(
        st.lists(
            st.text(
                alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
                min_size=1,
                max_size=8,
            ),
            min_size=n,
            max_size=n,
            unique=True,
        )
    )
    # Distinct starts (kept simple; uniqueness avoids start ties so the
    # expected ordering is fully determined and easy to assert).
    starts = draw(
        st.lists(
            st.floats(min_value=0.0, max_value=500.0, allow_nan=False, allow_infinity=False),
            min_size=n,
            max_size=n,
            unique=True,
        )
    )

    atoms = []
    for i in range(n):
        concept = f"c{i}"
        prereq = f"c{(i + 1) % n}"  # depends on the next concept -> closes the loop
        atoms.append(
            LearningAtom(
                id=ids[i],
                topic_slug="cycle-topic",
                video_id="vid-1",
                source_url="https://example.com/v",
                role=_CYCLE_ROLE,
                concept=concept,
                prior_knowledge=[prereq],
                start=starts[i],
                end=starts[i] + 1.0,
            )
        )

    # Shuffle so the input order does not coincide with the expected output.
    atoms = draw(st.permutations(atoms))
    return list(atoms)


def _planned_arc():
    # Single-role planned arc matching every atom's role; role-ordinal is
    # therefore identical across all cycle atoms.
    return PlannedArc(
        topic_slug="cycle-topic",
        concept_type="conceptual",
        roles=[ArcRole(role=_CYCLE_ROLE, ordinal=1)],
    )


class TestCircularDependencies:
    @settings(max_examples=100)
    @given(atoms=_cycle_atoms())
    def test_cycle_atoms_retained_ordered_and_flagged(self, atoms):
        ordered, defects = topological_order(atoms, _planned_arc())

        input_ids = {a.id for a in atoms}
        output_ids = {a.id for a in ordered}

        # (a) All input atom ids present in output — none dropped.
        assert output_ids == input_ids
        assert len(ordered) == len(atoms)

        # (b) A circular_dependency defect is recorded.
        assert any(d.defect_type == "circular_dependency" for d in defects)

        # (c) The cycle members appear in (start ASC, id ASC) order.
        cycle_in_output = [(a.start, a.id) for a in ordered]
        assert cycle_in_output == sorted(cycle_in_output)
