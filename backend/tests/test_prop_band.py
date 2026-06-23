"""Property-based test for deterministic Score_Band computation.

# Feature: adaptive-learning-game, Property 1: Score banding is a total, correct partition

For any score fraction in ``[0, 1]``, ``band(score_pct)`` returns exactly one
band, and it is ``DESCEND`` when ``score_pct < 0.40``, ``REINFORCE`` when
``0.40 <= score_pct <= 0.70`` (inclusive at both ends), and ``CLIMB`` when
``score_pct > 0.70``. The mapping is deterministic (same input always yields the
same band).

Validates: Requirements 3.1, 3.2, 3.3, 3.4
"""
from hypothesis import given, settings, strategies as st

from app.services.game import band

_BANDS = {"DESCEND", "REINFORCE", "CLIMB"}

# Score fractions span the inclusive range [0, 1]; allow_subnormal kept default.
_score = st.floats(
    min_value=0.0,
    max_value=1.0,
    allow_nan=False,
    allow_infinity=False,
)


def _expected_band(score_pct: float) -> str:
    if score_pct < 0.40:
        return "DESCEND"
    if score_pct <= 0.70:
        return "REINFORCE"
    return "CLIMB"


class TestScoreBandingTotalCorrectPartition:
    @settings(max_examples=200)
    @given(score_pct=_score)
    def test_band_is_total_and_correct(self, score_pct):
        result = band(score_pct)
        # Exactly one band per input: the result is always one of the three.
        assert result in _BANDS
        # Correct partition by the deterministic thresholds.
        assert result == _expected_band(score_pct)

    @settings(max_examples=200)
    @given(score_pct=_score)
    def test_band_is_deterministic(self, score_pct):
        # Same input always yields the same band.
        assert band(score_pct) == band(score_pct)

    def test_lower_boundary_0_40_is_reinforce(self):
        # Explicit boundary case: 0.40 is inclusive lower edge of REINFORCE.
        assert band(0.40) == "REINFORCE"

    def test_upper_boundary_0_70_is_reinforce(self):
        # Explicit boundary case: 0.70 is inclusive upper edge of REINFORCE.
        assert band(0.70) == "REINFORCE"

    def test_just_below_0_40_is_descend(self):
        assert band(0.3999) == "DESCEND"

    def test_just_above_0_70_is_climb(self):
        assert band(0.7001) == "CLIMB"
