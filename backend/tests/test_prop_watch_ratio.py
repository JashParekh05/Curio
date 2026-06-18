"""Property-based test for watch-ratio totality, clamping, and shared formula.

# Feature: engagement-telemetry, Property 1: Watch_Ratio is total, clamped, and matches the shared formula

For any ``watch_ms`` (including ``None`` and negatives) and any
``duration_seconds`` (including ``None``, ``0``, and negatives),
``telemetry.watch_ratio``:

  - returns ``None`` when the duration is ``None`` or ``<= 0``,
  - otherwise returns a value in the inclusive range ``0.0`` to ``1.0`` equal to
    ``min(max(watch_ms/1000/duration, 0.0), 1.0)`` — exactly ``1.0`` when watch
    exceeds full duration, exactly ``0.0`` when ``watch_ms <= 0`` (or ``None``),
  - and the result equals the original ``analytics._watch_ratio`` for the same
    inputs, so ``compute_dropoff`` is unchanged.

Validates: Requirements 7.2, 7.3, 7.4, 7.5, 6.5
"""
from hypothesis import given, settings, strategies as st

from app.services import telemetry
from app.services.analytics import _watch_ratio as analytics_watch_ratio

# watch_ms: realistic milliseconds plus None and negatives and zero.
_watch_ms = st.one_of(
    st.none(),
    st.integers(min_value=-10_000, max_value=10_000_000),
)

# duration_seconds: includes None, 0, negatives, and positive durations.
_duration = st.one_of(
    st.none(),
    st.integers(min_value=-100, max_value=3_600),
)


class TestWatchRatio:
    @settings(max_examples=100)
    @given(watch_ms=_watch_ms, duration_seconds=_duration)
    def test_total_clamped_and_matches_shared_formula(self, watch_ms, duration_seconds):
        result = telemetry.watch_ratio(watch_ms, duration_seconds)

        # Undefined when duration is unknown or non-positive (Req 7.2).
        if duration_seconds is None or duration_seconds <= 0:
            assert result is None
        # Undefined when there is no measurement (watch_ms is None) — this is the
        # shared definition that keeps compute_dropoff unchanged (Req 6.5).
        elif watch_ms is None:
            assert result is None
        else:
            # Defined: must be a float in [0.0, 1.0] (Req 7.5).
            assert result is not None
            assert isinstance(result, float)
            assert 0.0 <= result <= 1.0

            expected = min(max(watch_ms / 1000.0 / duration_seconds, 0.0), 1.0)
            assert result == expected

            # Exactly 0.0 when watch_ms is at or below zero (Req 7.4).
            if watch_ms <= 0:
                assert result == 0.0

            # Exactly 1.0 when watch exceeds full duration (Req 7.3).
            elif watch_ms / 1000.0 > duration_seconds:
                assert result == 1.0

        # Equals the original analytics definition for the same inputs (Req 6.5).
        assert result == analytics_watch_ratio(watch_ms, duration_seconds)
