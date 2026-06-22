"""Property-based test for score clamping and defaulting.

# Feature: content-hook-engagement, Property 16: Score clamping and defaulting

For any input value, ``clamp_score`` returns a value in ``[0.0, 1.0]`` rounded
to 2 decimal places:
  - an in-range numeric maps to its nearest 2-dp value,
  - an out-of-range numeric maps to the nearest bound (>1 -> 1.0, <0 -> 0.0),
  - a missing (None) or non-numeric input maps to 0.5,
  - NaN / inf are treated as non-numeric/out-of-range per the clamp rules.

Validates: Requirements 4.3, 7.8, 7.9
"""
import math

from hypothesis import given, settings, strategies as st

from app.services.coherence import clamp_score

# ---------------------------------------------------------------------------
# Smart generators — cover the full input space:
#   - floats in range, out of range (negative and > 1), NaN, inf,
#   - ints (in and out of range),
#   - None,
#   - numeric and non-numeric strings,
#   - other objects (lists, dicts) that cannot be coerced to float.
# ---------------------------------------------------------------------------

_in_range_float = st.floats(
    min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False
)
_out_of_range_float = st.one_of(
    st.floats(min_value=-1e6, max_value=-1e-6, allow_nan=False, allow_infinity=False),
    st.floats(min_value=1.0 + 1e-6, max_value=1e6, allow_nan=False, allow_infinity=False),
)
_special_floats = st.sampled_from(
    [float("nan"), float("inf"), float("-inf")]
)
_ints = st.integers(min_value=-1000, max_value=1000)
_numeric_strings = st.builds(
    str,
    st.one_of(
        st.floats(min_value=-1e3, max_value=1e3, allow_nan=False, allow_infinity=False),
        st.integers(min_value=-1000, max_value=1000),
    ),
)
def _is_numeric(value) -> bool:
    """Return True if value can be coerced to a finite-or-infinite float."""
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


# Non-numeric strings: text that cannot be parsed as a float.
_non_numeric_text = st.text(max_size=20).filter(lambda s: not _is_numeric(s))

# Other (non-numeric, non-string) objects that float() cannot handle.
_other_objects = st.one_of(
    st.lists(st.integers(), max_size=3),
    st.dictionaries(st.text(max_size=3), st.integers(), max_size=2),
    st.tuples(st.integers()),
)

_any_input = st.one_of(
    _in_range_float,
    _out_of_range_float,
    _special_floats,
    _ints,
    _numeric_strings,
    _non_numeric_text,
    st.none(),
    _other_objects,
)


class TestScoreClamping:
    @settings(max_examples=100)
    @given(value=_any_input)
    def test_clamp_score_bounds_and_defaulting(self, value):
        result = clamp_score(value)

        # Result is always a float in [0.0, 1.0].
        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0

        # Result is rounded to 2 decimal places.
        assert result == round(result, 2)

        # None / non-numeric / NaN -> 0.5.
        if value is None or not _is_numeric(value):
            assert result == 0.5
            return

        numeric = float(value)

        if math.isnan(numeric):
            assert result == 0.5
        elif numeric > 1.0:
            # Out of range high (incl +inf) clamps to 1.0.
            assert result == 1.0
        elif numeric < 0.0:
            # Out of range low (incl -inf) clamps to 0.0.
            assert result == 0.0
        else:
            # In-range numeric maps to its nearest 2-dp value.
            assert result == round(numeric, 2)
