"""Property-based test for Watch_Quality bound.

# Feature: content-retrieval-revamp, Property 14: Watch_Quality is the bounded view-weighted average

*For any* list of a Topic's Watch_Events, ``topic_watch_quality`` equals the
mean of the per-event Watch_Ratios computed with the shared engagement-telemetry
``watch_ratio`` definition (the view-weighted average, since each event is one
view), and that value lies in the inclusive range [0.0, 1.0]. When the Topic has
zero Watch_Events -- or no event with a defined Watch_Ratio -- the Watch_Quality
is the undefined sentinel (Req 4.6, 4.7).

Imports only the pure module under test (``coverage_view``) and the shared pure
``telemetry.watch_ratio`` definition it must match, so it runs offline with no
external service.

Validates: Requirements 4.6
"""
from hypothesis import given, settings, strategies as st

from app.services.coverage_view import (
    UNDEFINED_WATCH_QUALITY,
    topic_watch_quality,
)
from app.services.telemetry import watch_ratio

# (watch_ms, duration_seconds) with strictly-positive duration so every event
# has a defined Watch_Ratio in [0, 1].
_defined_event = st.tuples(
    st.integers(min_value=0, max_value=1_200_000),
    st.integers(min_value=1, max_value=600),
)
# (watch_ms, duration_seconds) where duration <= 0 makes the ratio undefined.
_undefined_event = st.tuples(
    st.integers(min_value=0, max_value=1_200_000),
    st.integers(min_value=-10, max_value=0),
)


class TestWatchQualityBound:
    @settings(max_examples=100)
    @given(events=st.lists(_defined_event, min_size=1, max_size=20))
    def test_quality_equals_bounded_view_weighted_average(self, events):
        quality = topic_watch_quality(events)

        ratios = [watch_ratio(ms, dur) for ms, dur in events]
        ratios = [r for r in ratios if r is not None]
        expected = sum(ratios) / len(ratios)

        assert quality == expected
        assert 0.0 <= quality <= 1.0

    @settings(max_examples=100)
    @given(events=st.lists(_undefined_event, max_size=20))
    def test_no_defined_event_is_undefined(self, events):
        # Zero events, or events whose Watch_Ratio is undefined, yield the
        # sentinel -- distinguishable from every numeric value in [0.0, 1.0].
        assert topic_watch_quality(events) is UNDEFINED_WATCH_QUALITY

    @settings(max_examples=100)
    @given(
        defined=st.lists(_defined_event, min_size=1, max_size=10),
        undefined=st.lists(_undefined_event, max_size=10),
    )
    def test_undefined_events_excluded_from_average(self, defined, undefined):
        # Undefined-ratio events contribute nothing: quality over the mix equals
        # quality over only the defined events.
        mixed = defined + undefined
        assert topic_watch_quality(mixed) == topic_watch_quality(defined)

    def test_zero_events_is_undefined(self):
        assert topic_watch_quality([]) is UNDEFINED_WATCH_QUALITY
