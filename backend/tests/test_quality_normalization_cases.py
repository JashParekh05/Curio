"""Unit tests for ``normalize_quality_score`` concrete cases.

Pins the specific defaulting and clamping behavior of the Per_Segment_Judge's
quality-score normalization: missing/None/non-numeric/NaN -> 0.5, and
out-of-range numerics clamp to the nearer of 0.0/1.0.

Validates: Requirements 3.3
"""
from app.services.segment_judge import normalize_quality_score


class TestNormalizeQualityScoreCases:
    def test_none_defaults_to_half(self):
        assert normalize_quality_score(None) == 0.5

    def test_empty_string_defaults_to_half(self):
        assert normalize_quality_score("") == 0.5

    def test_non_numeric_string_defaults_to_half(self):
        assert normalize_quality_score("abc") == 0.5

    def test_nan_defaults_to_half(self):
        assert normalize_quality_score(float("nan")) == 0.5

    def test_below_range_clamps_to_zero(self):
        assert normalize_quality_score(-0.3) == 0.0

    def test_above_range_clamps_to_one(self):
        assert normalize_quality_score(1.7) == 1.0
