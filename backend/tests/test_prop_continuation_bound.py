"""Property-based test for the repair-loop continuation bound.

# Feature: content-hook-engagement, Property 19: Repair loop continuation bound

For any defect list, completed-round count, and configured maximum,
``should_continue`` returns true if and only if the defect list is non-empty
AND the completed-round count is below ``clamp(max, 1, 10)``. Consequently the
loop runs at most ``clamp(max, 1, 10)`` rounds.

Validates: Requirements 4.6
"""
from hypothesis import given, settings, strategies as st

from app.models.schemas import CoherenceDefect, DefectType, PedagogicalRole
from app.services.coherence import should_continue

# All defined defect types and pedagogical roles (for building well-formed defects).
_DEFECT_TYPES: list[DefectType] = [
    "prerequisite_gap", "conceptual_jump", "contradiction",
    "redundancy", "unfilled_role", "circular_dependency", "missing_piece",
]
_ROLES: list[PedagogicalRole] = [
    "problem_statement", "meaning", "visualization", "approach",
    "worked_example", "edge_cases",
    "definition", "motivation", "mechanism", "example", "common_misconception",
]

# A single well-formed CoherenceDefect.
_defect = st.builds(
    CoherenceDefect,
    defect_type=st.sampled_from(_DEFECT_TYPES),
    clip_positions=st.lists(st.integers(min_value=1, max_value=20), max_size=5),
    role=st.one_of(st.none(), st.sampled_from(_ROLES)),
)

# Defect lists: 0..k defects (includes the empty list).
_defects = st.lists(_defect, min_size=0, max_size=8)

# Completed-round counts: include negatives, zero, in-range, and large values.
_completed_rounds = st.integers(min_value=-5, max_value=50)

# Configured maximums: include values below 1 (and negative), in-range, and above 10.
_max_rounds = st.integers(min_value=-5, max_value=50)


class TestContinuationBound:
    @settings(max_examples=100)
    @given(
        defects=_defects,
        completed_rounds=_completed_rounds,
        max_rounds=_max_rounds,
    )
    def test_should_continue_iff_defects_and_under_clamped_max(
        self, defects, completed_rounds, max_rounds
    ):
        result = should_continue(defects, completed_rounds, max_rounds)

        effective_max = max(1, min(10, max_rounds))
        expected = (len(defects) > 0) and (completed_rounds < effective_max)

        assert result == expected
        assert isinstance(result, bool)

    @settings(max_examples=100)
    @given(
        completed_rounds=_completed_rounds,
        max_rounds=_max_rounds,
    )
    def test_empty_defects_never_continues(self, completed_rounds, max_rounds):
        # No defects -> always stop, regardless of round counts (Req 4.6).
        assert should_continue([], completed_rounds, max_rounds) is False

    @settings(max_examples=100)
    @given(
        defects=_defects.filter(lambda d: len(d) > 0),
        max_rounds=_max_rounds,
    )
    def test_loop_bounded_by_clamped_max(self, defects, max_rounds):
        # With defects always present, the loop advances only while
        # completed_rounds < clamp(max, 1, 10); the cap is the clamped max.
        effective_max = max(1, min(10, max_rounds))

        rounds = 0
        # Simulate the loop guard: keep going while should_continue holds.
        while should_continue(defects, rounds, max_rounds):
            rounds += 1
            # Safety stop well above any possible clamped cap.
            assert rounds <= 11

        # The loop ran exactly clamp(max, 1, 10) rounds when defects persist.
        assert rounds == effective_max
        assert 1 <= rounds <= 10
