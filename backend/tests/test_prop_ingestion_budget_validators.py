"""Property-based test for the ingestion budget & retry/timeout validators.

# Feature: deep-content-ingestion, Property 15: Budget and retry/timeout validators clamp to range with defaults

For any raw input value:
  * ``validate_model_budget`` returns an int in [1, 100], defaulting to 20
    (``DEFAULT_MODEL_BUDGET``) when the value is missing or invalid.
  * ``validate_retries`` returns an int in [0, 5], defaulting to 2
    (``DEFAULT_RETRIES``) when the value is missing or invalid.
  * ``validate_timeout`` returns an int in [1, 60], defaulting to 10
    (``DEFAULT_TIMEOUT_SECONDS``) when the value is missing or invalid.

Validates: Requirements 9.2, 9.3
"""
import math

from hypothesis import given, settings, strategies as st

from app.services.ingestion_budget import (
    DEFAULT_MODEL_BUDGET,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT_SECONDS,
    validate_model_budget,
    validate_retries,
    validate_timeout,
)


# ---------------------------------------------------------------------------
# Smart raw-input generator.
#
# Each validator accepts `object` (any type) and must either coerce it to an
# int and clamp, or fall back to its default. The generator intelligently
# spans the real input space, deliberately mixing the branches the coercion
# logic distinguishes:
#   * valid ints across and beyond the clamp ranges (negatives, zero, huge)
#   * bool values (must be rejected -> default, since bool is an int subclass)
#   * integral and non-integral floats, plus NaN / +-inf
#   * numeric and non-numeric strings (incl. whitespace-padded)
#   * None and arbitrary objects
# ---------------------------------------------------------------------------
def _is_valid_int(raw: object) -> bool:
    """Mirror the module's coercion contract: does `raw` represent an int?"""
    if isinstance(raw, bool):
        return False
    if isinstance(raw, int):
        return True
    if isinstance(raw, float):
        if math.isnan(raw) or math.isinf(raw):
            return False
        return raw == int(raw)
    if isinstance(raw, str):
        try:
            int(raw.strip())
            return True
        except (ValueError, TypeError):
            return False
    return False


def _coerced_value(raw: object) -> int:
    """The int the validators will derive from a valid raw input."""
    if isinstance(raw, str):
        return int(raw.strip())
    return int(raw)


_raw_st = st.one_of(
    st.integers(min_value=-1000, max_value=1000),          # ints across ranges
    st.booleans(),                                         # rejected -> default
    st.floats(allow_nan=True, allow_infinity=True),        # incl. NaN / inf
    st.floats(min_value=-50, max_value=200).map(float),    # integral & non-integral
    st.text(max_size=8),                                   # numeric & non-numeric
    st.integers(min_value=-30, max_value=70).map(str),     # numeric strings
    st.none(),                                             # missing
    st.just(object()),                                     # arbitrary object
)


class TestIngestionBudgetValidators:
    @settings(max_examples=100)
    @given(raw=_raw_st)
    def test_validate_model_budget(self, raw):
        result = validate_model_budget(raw)
        assert isinstance(result, int) and not isinstance(result, bool)
        assert 1 <= result <= 100
        if _is_valid_int(raw):
            assert result == max(1, min(100, _coerced_value(raw)))
        else:
            assert result == DEFAULT_MODEL_BUDGET == 20

    @settings(max_examples=100)
    @given(raw=_raw_st)
    def test_validate_retries(self, raw):
        result = validate_retries(raw)
        assert isinstance(result, int) and not isinstance(result, bool)
        assert 0 <= result <= 5
        if _is_valid_int(raw):
            assert result == max(0, min(5, _coerced_value(raw)))
        else:
            assert result == DEFAULT_RETRIES == 2

    @settings(max_examples=100)
    @given(raw=_raw_st)
    def test_validate_timeout(self, raw):
        result = validate_timeout(raw)
        assert isinstance(result, int) and not isinstance(result, bool)
        assert 1 <= result <= 60
        if _is_valid_int(raw):
            assert result == max(1, min(60, _coerced_value(raw)))
        else:
            assert result == DEFAULT_TIMEOUT_SECONDS == 10
