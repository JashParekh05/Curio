"""Property-based test for uncovered prior knowledge handling.

# Feature: content-hook-engagement, Property 11: Uncovered prior knowledge is retained and flagged

For any selected atoms where some atom lists a ``prior_knowledge`` concept that
no earlier atom covers, the assembly output retains that atom in the sequence
and records a coherence defect naming the uncovered concept.

Validates: Requirements 3.4
"""
from typing import get_args

from hypothesis import given, settings, strategies as st

from app.models.schemas import ArcRole, LearningAtom, PedagogicalRole, PlannedArc
from app.services.arc_assembler import topological_order

_VALID_ROLES = list(get_args(PedagogicalRole))

# Concepts that atoms may *cover*.
_CONCEPTS = ["alpha", "beta", "gamma", "delta"]

# Concept labels guaranteed to be absent from ``_CONCEPTS`` — no generated atom
# ever covers one of these, so listing one as prior_knowledge is always an
# uncovered prerequisite.
_ABSENT = ["absent_one", "absent_two", "absent_three", "absent_four"]


def _planned_arc() -> PlannedArc:
    """A Planned_Arc spanning every defined role with consecutive ordinals."""
    return PlannedArc(
        topic_slug="topic",
        concept_type="default",
        roles=[ArcRole(role=r, ordinal=i + 1) for i, r in enumerate(_VALID_ROLES)],
    )


@st.composite
def _atom_pool_with_uncovered(draw):
    """Generate a pool of atoms in which at least one atom lists a
    guaranteed-absent (uncovered) concept in its prior_knowledge."""
    n = draw(st.integers(min_value=1, max_value=20))
    atoms: list[LearningAtom] = []
    for i in range(n):
        concept = draw(st.sampled_from(_CONCEPTS))
        role = draw(st.sampled_from(_VALID_ROLES))
        start = draw(
            st.floats(min_value=0.0, max_value=500.0, allow_nan=False, allow_infinity=False)
        )
        # Optionally include some absent prerequisites for this atom.
        prior = draw(
            st.lists(st.sampled_from(_ABSENT), min_size=0, max_size=3, unique=True)
        )
        atoms.append(
            LearningAtom(
                id=f"atom-{i}",  # unique within the pool
                topic_slug="topic",
                video_id="vid",
                source_url="https://example.com/v",
                role=role,
                concept=concept,
                prior_knowledge=prior,
                start=start,
                end=start + 5.0,
            )
        )

    # Guarantee the premise of Property 11: force at least one atom to list an
    # uncovered (absent) prerequisite.
    target = draw(st.integers(min_value=0, max_value=n - 1))
    forced = draw(st.sampled_from(_ABSENT))
    if forced not in atoms[target].prior_knowledge:
        atoms[target].prior_knowledge.append(forced)

    return atoms


class TestUncoveredPriorKnowledge:
    @settings(max_examples=100)
    @given(atoms=_atom_pool_with_uncovered())
    def test_uncovered_prior_knowledge_retained_and_flagged(self, atoms):
        planned_arc = _planned_arc()

        ordered, defects = topological_order(atoms, planned_arc)

        # (a) Every input atom id is still present in the ordered output —
        #     nothing is dropped when a prerequisite is uncovered (Req 3.4).
        input_ids = {a.id for a in atoms}
        ordered_ids = {a.id for a in ordered}
        assert ordered_ids == input_ids
        # No atom dropped or duplicated.
        assert len(ordered) == len(atoms)

        # Sanity: an uncovered prerequisite genuinely exists (no atom covers
        # any of the absent labels), guaranteed by the generator.
        covered = {a.concept for a in atoms}
        uncovered_exists = any(
            pk for a in atoms for pk in a.prior_knowledge if pk not in covered
        )
        assert uncovered_exists

        # (b) At least one prerequisite_gap defect is recorded for the
        #     uncovered prior_knowledge (Req 3.4).
        assert any(d.defect_type == "prerequisite_gap" for d in defects)
