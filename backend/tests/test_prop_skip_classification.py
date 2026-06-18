"""Property-based test for skip classification.

# Feature: engagement-telemetry, Property 2: Skip classification

For any watch ratio (defined or ``None``), ``has_watch_event`` flag, and
``skip_threshold`` in ``[0.0, 1.0]``, ``telemetry.classify_skip`` returns ``True``
exactly when there is no Watch_Event OR the ratio is defined and
``<= skip_threshold``, and returns ``False`` only when the ratio is defined and
strictly above the threshold.

Validates: Requirements 3.5, 3.6, 3.7
"""
from hypothesis import given, settings, strategies as st

from app.services import telemetry

# ratio: a defined value in [0.0, 1.0], plus None (undefined / no watch event).
_ratio = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)

_threshold = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


class TestSkipClassification:
    @settings(max_examples=100)
    @given(ratio=_ratio, has_watch_event=st.booleans(), skip_threshold=_threshold)
    def test_skip_iff_no_event_or_ratio_at_or_below_threshold(
        self, ratio, has_watch_event, skip_threshold
    ):
        result = telemetry.classify_skip(ratio, has_watch_event, skip_threshold)

        # The exact specification of a Skip (Req 3.5, 3.6, 3.7): an entry is a
        # Skip when there is no Watch_Event, or when the ratio is undefined, or
        # when the defined ratio is at or below the threshold. Equivalently, an
        # entry is NOT a Skip only when the ratio is defined and strictly above
        # the threshold.
        expected = (not has_watch_event) or (ratio is None) or (ratio <= skip_threshold)
        assert result is expected

        # False is produced ONLY when the ratio is defined and strictly above
        # the threshold (and a watch event exists) (Req 3.7).
        if result is False:
            assert has_watch_event
            assert ratio is not None
            assert ratio > skip_threshold
