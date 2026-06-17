"""Property-based test for Fast_Preview limit validation.

# Feature: deep-content-ingestion, Property 14: Fast_Preview limit validation

``validate_fast_preview_limit`` returns ``(value, None)`` for an integer in the
inclusive range [1, 10], and falls back to ``(3, 'fast_preview_limit_out_of_range')``
for any out-of-range integer or invalid type (None, bool, non-numeric, float that
is not a whole integer in range, etc.).

Validates: Requirements 6.2, 6.8
"""
from hypothesis import given, settings, strategies as st

from app.services.ingestion_state import (
    DEFAULT_FAST_PREVIEW_LIMIT,
    validate_fast_preview_limit,
)

OUT_OF_RANGE_ERROR = "fast_preview_limit_out_of_range"


class TestFastPreviewLimit:
    @settings(max_examples=100)
    @given(value=st.integers(min_value=1, max_value=10))
    def test_in_range_int_accepted(self, value):
        # A whole integer in [1, 10] is accepted unchanged with no error (Req 6.2).
        limit, error = validate_fast_preview_limit(value)
        assert limit == value
        assert error is None

    @settings(max_examples=100)
    @given(
        value=st.integers().filter(lambda v: v < 1 or v > 10),
    )
    def test_out_of_range_int_falls_back(self, value):
        # Out-of-range integers fall back to default with the error code (Req 6.8).
        limit, error = validate_fast_preview_limit(value)
        assert limit == DEFAULT_FAST_PREVIEW_LIMIT == 3
        assert error == OUT_OF_RANGE_ERROR

    @settings(max_examples=100)
    @given(
        raw=st.one_of(
            st.none(),
            st.booleans(),
            # Text that cannot be coerced to an int in range.
            st.text(alphabet="abcdefghij ", min_size=1),
            st.just(float("nan")),
            st.just(float("inf")),
        )
    )
    def test_invalid_types_fall_back(self, raw):
        # None / bool / non-numeric text / non-finite floats are invalid and
        # fall back to the default with the out-of-range error code (Req 6.8).
        # (Booleans are explicitly rejected even though bool is an int subclass.)
        limit, error = validate_fast_preview_limit(raw)
        assert limit == DEFAULT_FAST_PREVIEW_LIMIT == 3
        assert error == OUT_OF_RANGE_ERROR

    @settings(max_examples=100)
    @given(
        raw=st.one_of(
            st.integers(),
            st.none(),
            st.booleans(),
            st.text(),
            st.floats(allow_nan=False, allow_infinity=False),
        )
    )
    def test_result_shape_is_total(self, raw):
        # Always returns a (int, str|None) pair; the limit is always a valid limit.
        limit, error = validate_fast_preview_limit(raw)
        assert isinstance(limit, int)
        assert error is None or error == OUT_OF_RANGE_ERROR
        if error is None:
            assert 1 <= limit <= 10
        else:
            assert limit == DEFAULT_FAST_PREVIEW_LIMIT
