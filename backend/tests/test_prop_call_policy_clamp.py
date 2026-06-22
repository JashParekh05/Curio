"""Property-based test for Call_Policy clamping.

# Feature: content-retrieval-revamp, Property 34: Call policy clamps to the valid ranges

*For any* requested retry count and timeout, ``validate_call_policy`` returns a
``CallPolicy`` whose ``max_retries`` lies in the inclusive range 1 to 5 and whose
``timeout_ms`` lies in the inclusive range 1000 to 30000 milliseconds (Req 11.3).

Imports only the pure module under test (``call_policy``), so it runs offline with
no external service.

Validates: Requirements 11.3, 11.7
"""
from hypothesis import given, settings, strategies as st

from app.services.call_policy import (
    MAX_RETRIES,
    MAX_TIMEOUT_MS,
    MIN_RETRIES,
    MIN_TIMEOUT_MS,
    CallPolicy,
    validate_call_policy,
)

# Span well below, within, and well above each valid range so the strategy
# exercises both clamp directions and the pass-through interior.
_retries = st.integers(min_value=-1000, max_value=1000)
_timeouts = st.integers(min_value=-100_000, max_value=1_000_000)


class TestCallPolicyClamp:
    @settings(max_examples=100)
    @given(max_retries=_retries, timeout_ms=_timeouts)
    def test_max_retries_in_range(self, max_retries, timeout_ms):
        policy = validate_call_policy(max_retries, timeout_ms)
        assert MIN_RETRIES <= policy.max_retries <= MAX_RETRIES
        assert 1 <= policy.max_retries <= 5

    @settings(max_examples=100)
    @given(max_retries=_retries, timeout_ms=_timeouts)
    def test_timeout_in_range(self, max_retries, timeout_ms):
        policy = validate_call_policy(max_retries, timeout_ms)
        assert MIN_TIMEOUT_MS <= policy.timeout_ms <= MAX_TIMEOUT_MS
        assert 1000 <= policy.timeout_ms <= 30000

    @settings(max_examples=100)
    @given(max_retries=_retries, timeout_ms=_timeouts)
    def test_returns_call_policy_and_is_deterministic(self, max_retries, timeout_ms):
        first = validate_call_policy(max_retries, timeout_ms)
        second = validate_call_policy(max_retries, timeout_ms)
        assert isinstance(first, CallPolicy)
        # Pure: identical inputs yield identical outputs (Req 11.7).
        assert first == second

    @settings(max_examples=100)
    @given(
        max_retries=st.integers(min_value=MIN_RETRIES, max_value=MAX_RETRIES),
        timeout_ms=st.integers(min_value=MIN_TIMEOUT_MS, max_value=MAX_TIMEOUT_MS),
    )
    def test_in_range_values_pass_through_unchanged(self, max_retries, timeout_ms):
        # A request already inside the valid ranges is returned unchanged.
        policy = validate_call_policy(max_retries, timeout_ms)
        assert policy.max_retries == max_retries
        assert policy.timeout_ms == timeout_ms
