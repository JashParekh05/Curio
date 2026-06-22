"""Property-based test for deterministic atom selection.

# Feature: content-hook-engagement, Property 7: Selection is one-per-(concept, role) and order-independent

For any pool of Learning_Atoms, ``select_atoms`` returns exactly one atom for
each ``(covered concept, role)`` combination present in the pool, and permuting
the input pool yields the identical selection.

Validates: Requirements 3.5
"""
from typing import get_args

from hypothesis import given, settings, strategies as st

from app.models.schemas import LearningAtom, PedagogicalRole
from app.services.arc_assembler import select_atoms

_VALID_ROLES = list(get_args(PedagogicalRole))

# Small concept set so collisions on (concept, role) are common and the
# one-per-pair / tiebreaker logic is exercised heavily.
_CONCEPTS = ["alpha", "beta", "gamma", "delta"]


@st.composite
def _atom_pool(draw):
    """Generate a pool of LearningAtoms with unique ids, random concept from a
    small set, random role, and random start timestamps."""
    n = draw(st.integers(min_value=0, max_value=25))
    atoms: list[LearningAtom] = []
    for i in range(n):
        concept = draw(st.sampled_from(_CONCEPTS))
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
                concept=concept,
                prior_knowledge=[],
                start=start,
                end=start + 5.0,
            )
        )
    return atoms


def _mapping(atoms: list[LearningAtom]) -> dict[tuple[str, str], str]:
    """(concept, role) -> selected atom id."""
    return {(a.concept, a.role): a.id for a in atoms}


class TestDeterministicSelection:
    @settings(max_examples=100)
    @given(atoms=_atom_pool(), seed=st.integers())
    def test_one_per_pair_and_order_independent(self, atoms, seed):
        selected = select_atoms(atoms)

        # --- Exactly one atom per distinct (concept, role) present in pool. ---
        present_pairs = {(a.concept, a.role) for a in atoms}
        selected_pairs = [(a.concept, a.role) for a in selected]

        # No duplicate (concept, role) in the selection.
        assert len(selected_pairs) == len(set(selected_pairs))
        # Selection covers exactly the pairs present in the pool.
        assert set(selected_pairs) == present_pairs

        # --- Order independence: permuting the pool yields identical selection. ---
        import random

        shuffled = list(atoms)
        random.Random(seed).shuffle(shuffled)
        selected_shuffled = select_atoms(shuffled)

        # Compare by (concept, role) -> id mapping: must be identical.
        assert _mapping(selected) == _mapping(selected_shuffled)
        # And the chosen id-set is identical.
        assert {a.id for a in selected} == {a.id for a in selected_shuffled}
