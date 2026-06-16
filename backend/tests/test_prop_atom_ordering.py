"""Property-based test for atom ordering.

# Feature: content-hook-engagement, Property 6: Ordered atoms are start-ascending and non-overlapping

For any list of Learning_Atoms, ``order_atoms`` returns them sorted by ascending
start timestamp with no two atoms overlapping in time.

Validates: Requirements 2.6
"""
from typing import get_args

from hypothesis import given, settings, strategies as st

from app.services.pipeline import order_atoms
from app.models.schemas import LearningAtom, PedagogicalRole

_VALID_ROLES = list(get_args(PedagogicalRole))


# ---------------------------------------------------------------------------
# Smart generator — construct valid LearningAtom objects with non-negative
# start, end strictly greater than start, and random roles/concepts. Starts and
# durations are drawn from a small bounded range so generated atom lists are
# very likely to overlap, exercising the overlap-resolution path.
# ---------------------------------------------------------------------------
@st.composite
def _learning_atom(draw):
    start = draw(
        st.floats(min_value=0.0, max_value=60.0, allow_nan=False, allow_infinity=False)
    )
    # end > start, both >= 0.
    duration = draw(
        st.floats(min_value=0.1, max_value=30.0, allow_nan=False, allow_infinity=False)
    )
    end = start + duration
    return LearningAtom(
        id=draw(st.text(min_size=1, max_size=12)),
        topic_slug="binary-search",
        video_id="vid-1",
        source_url="https://example.com/v",
        role=draw(st.sampled_from(_VALID_ROLES)),
        concept=draw(st.text(min_size=1, max_size=40)),
        prior_knowledge=draw(st.lists(st.text(min_size=1, max_size=10), max_size=5)),
        start=start,
        end=end,
    )


_atom_list = st.lists(_learning_atom(), max_size=12)


class TestAtomOrdering:
    @settings(max_examples=100)
    @given(atoms=_atom_list)
    def test_ordered_atoms_are_ascending_and_non_overlapping(self, atoms):
        result = order_atoms(atoms)

        # Sorted by ascending start timestamp.
        starts = [a.start for a in result]
        assert starts == sorted(starts)

        # No two consecutive atoms overlap in time.
        for i in range(len(result) - 1):
            assert result[i].end <= result[i + 1].start

        # Every returned atom remains internally well-formed (end > start).
        for a in result:
            assert a.start < a.end
