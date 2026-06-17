"""Property-based test for Quality_Score normalization.

# Feature: deep-content-ingestion, Property 6: Quality_Score normalization clamps, defaults, and rounds

``normalize_quality_score`` mirrors ``coherence.clamp_score``: a missing/None/
non-numeric/NaN raw value defaults to 0.5, an out-of-range numeric clamps to the
nearer of 0.0/1.0, and every result is a float in [0.0, 1.0] rounded to 2 dp.

Validates: Requirements 3.2, 3.3, 3.4
"""
import math

from hypothesis import given, settings, strategies as st

from app.services.segment_judge import normalize_quality_score


def _is_rounded_2dp(value: float) -> bool:
    """True iff value equals itself rounded to 2 decimal places."""
    return value == round(value, 2)


class TestQualityNormalization:
    @settings(max_examples=100)
    @given(
        raw=st.one_of(
            # In-range floats.
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
            # Out-of-range floats (below 0.0 and above 1.0).
            st.floats(min_value=-1000.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
            # Missing / non-numeric inputs.
            st.none(),
            st.text(),
            # NaN.
            st.just(float("nan")),
        )
    )
    def test_normalize_quality_score_invariants(self, raw):
        result = normalize_quality_score(raw)

        # Always a float in the closed unit interval, rounded to 2 dp.
        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0
        assert _is_rounded_2dp(result)

        # Missing / non-numeric / NaN -> default 0.5 (Req 3.3).
        is_numeric = isinstance(raw, (int, float)) and not isinstance(raw, bool)
        is_nan = isinstance(raw, float) and math.isnan(raw)
        if raw is None or not is_numeric or is_nan:
            assert result == 0.5
            return

        # Numeric inputs: clamp out-of-range to the nearer bound (Req 3.4),
        # otherwise round to 2 dp (Req 3.2).
        if raw < 0.0:
            assert result == 0.0
        elif raw > 1.0:
            assert result == 1.0
        else:
            assert result == round(raw, 2)

    @settings(max_examples=100)
    @given(
        raw=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
    )
    def test_in_range_values_round_to_2dp(self, raw):
        result = normalize_quality_score(raw)
        assert result == round(raw, 2)
        assert 0.0 <= result <= 1.0
