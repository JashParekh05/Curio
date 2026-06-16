"""Property-based test for the retry decision policy.

# Feature: content-hook-engagement, Property 33: Retry decision respects the clamped maximum

For any attempt index and configured maximum, ``next_retry_decision`` permits
another attempt if and only if the attempt index is below
``clamp(max_retries, 0, 5)``.

Validates: Requirements 8.2
"""
from hypothesis import given, settings, strategies as st

from app.services.coherence_budget import next_retry_decision


# Attempt indices: include negatives and large values to probe the full domain.
_attempt = st.integers(min_value=-100, max_value=1000)

# Configured maximum: include values below 0 and above 5 so the clamp matters.
_max_retries = st.integers(min_value=-100, max_value=100)


class TestRetryDecisionClampedMaximum:
    @settings(max_examples=100)
    @given(attempt=_attempt, max_retries=_max_retries)
    def test_retry_decision_respects_clamped_maximum(self, attempt, max_retries):
        effective_max = max(0, min(5, max_retries))
        assert next_retry_decision(attempt, max_retries) == (attempt < effective_max)
