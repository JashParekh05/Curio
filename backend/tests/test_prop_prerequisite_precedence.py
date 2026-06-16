"""Property-based test for prerequisite precedence in topological ordering.

# Feature: content-hook-engagement, Property 10: Prerequisites precede dependents

For any acyclic atom set, for every pair of atoms A and B where A's covered
concept appears in B's ``prior_knowledge``, A is placed earlier than B in the
ordered sequence produced by ``topological_order``.

Validates: Requirements 3.3
"""
from typing import get_args

from hypothesis import given, settings, strategies as st

from app.models.schemas import ArcRole, LearningAtom, PedagogicalRole, PlannedArc
from app.services.arc_assembler import topological_order

_VALID_ROLES = list(get_args(PedagogicalRole))


@st.composite
def _acyclic_atom_set(draw):
    """Generate an ACYCLIC set of LearningAtoms.

    Construction guaranteeing acyclicity:
      * ``n`` atoms, each with a distinct concept ``c0 .. c{n-1}``.
      * Atom ``i``'s ``prior_knowledge`` is a subset of ``{c0 .. c{i-1}}``
        (only concepts with a strictly lower index), so every dependency edge
        points "backwards" — no cycle can form.

    All atoms share the SAME role so that ordering is driven purely by the
    prerequisite graph (role ordinal is the primary sort key, so a single role
    isolates prerequisite logic).
    """
    n = draw(st.integers(min_value=0, max_value=12))
    role = draw(st.sampled_from(_VALID_ROLES))

    concepts = [f"c{i}" for i in range(n)]
    atoms: list[LearningAtom] = []
    for i in range(n):
        # prior_knowledge is a distinct subset of strictly-earlier concepts.
        earlier = concepts[:i]
        if earlier:
            prior = draw(
                st.lists(
                    st.sampled_from(earlier),
                    max_size=len(earlier),
                    unique=True,
                )
            )
        else:
            prior = []
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
                concept=concepts[i],
                prior_knowledge=prior,
                start=start,
                end=start + 5.0,
            )
        )

    # Shuffle so the input order does not coincide with the dependency order.
    perm = draw(st.permutations(atoms))
    return list(perm), role


class TestPrerequisitePrecedence:
    @settings(max_examples=100)
    @given(data=_acyclic_atom_set())
    def test_prerequisites_precede_dependents(self, data):
        atoms, role = data

        # Single-role planned arc so role ordinal is uniform and ordering is
        # driven purely by the prerequisite graph.
        planned_arc = PlannedArc(
            topic_slug="topic",
            concept_type="default",
            roles=[ArcRole(role=role, ordinal=1)],
        )

        ordered, _defects = topological_order(atoms, planned_arc)

        # Every input atom must appear exactly once in the output.
        assert {a.id for a in ordered} == {a.id for a in atoms}
        assert len(ordered) == len(atoms)

        # Position of each atom in the ordered sequence, keyed by its concept.
        position_by_concept = {a.concept: idx for idx, a in enumerate(ordered)}
        # And the set of concepts actually covered by some atom.
        covered = set(position_by_concept.keys())

        # For every atom B and each prerequisite concept covered by an atom A,
        # A must be placed strictly earlier than B.
        for b in ordered:
            b_pos = position_by_concept[b.concept]
            for prereq in b.prior_knowledge:
                if prereq in covered:
                    a_pos = position_by_concept[prereq]
                    assert a_pos < b_pos, (
                        f"prerequisite concept {prereq!r} (pos {a_pos}) does not "
                        f"precede dependent {b.concept!r} (pos {b_pos})"
                    )
