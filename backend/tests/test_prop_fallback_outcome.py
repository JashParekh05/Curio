"""Property-based test for Call_Policy fallback resolution.

# Feature: content-retrieval-revamp, Property 35: Exhausted or timed-out calls fall back without a learner-facing error

*For any* call whose retries are exhausted or whose timeout is exceeded,
``resolve_outcome`` returns the last successfully computed or previously stored
result and flags that the fallback was used, never signalling a learner-facing
error (Req 11.4). When neither condition holds the call produced a fresh success
and the fallback flag is not set. In every case a value is returned -- the
function never raises (Req 11.7).

Imports only the pure module under test (``call_policy``), so it runs offline with
no external service.

Validates: Requirements 11.4, 11.7
"""
from hypothesis import given, settings, strategies as st

from app.services.call_policy import CallOutcome, resolve_outcome

# A "last success" can be any stored result, including the absence of one (None).
_last_success = st.one_of(
    st.none(),
    st.integers(),
    st.text(),
    st.lists(st.integers(), max_size=5),
    st.dictionaries(st.text(max_size=3), st.integers(), max_size=3),
)
_flags = st.booleans()


class TestFallbackResolution:
    @settings(max_examples=100)
    @given(last_success=_last_success, attempts_exhausted=_flags, timed_out=_flags)
    def test_returns_best_available_result(self, last_success, attempts_exhausted, timed_out):
        outcome = resolve_outcome(last_success, attempts_exhausted, timed_out)
        # The best available result is always the supplied last success / stored
        # result -- the caller is never handed an error in its place (Req 11.4).
        assert isinstance(outcome, CallOutcome)
        assert outcome.value == last_success

    @settings(max_examples=100)
    @given(last_success=_last_success, attempts_exhausted=_flags, timed_out=_flags)
    def test_fallback_flagged_iff_exhausted_or_timed_out(
        self, last_success, attempts_exhausted, timed_out
    ):
        outcome = resolve_outcome(last_success, attempts_exhausted, timed_out)
        # used_fallback is True exactly when retries were exhausted OR the call
        # timed out (Req 11.4).
        assert outcome.used_fallback == (attempts_exhausted or timed_out)

    @settings(max_examples=100)
    @given(last_success=_last_success)
    def test_fresh_success_is_not_a_fallback(self, last_success):
        # Neither exhausted nor timed out: a fresh success, no fallback flag.
        outcome = resolve_outcome(last_success, False, False)
        assert outcome.used_fallback is False
        assert outcome.value == last_success

    @settings(max_examples=100)
    @given(
        last_success=_last_success,
        attempts_exhausted=_flags,
        timed_out=_flags,
    )
    def test_never_raises_and_is_deterministic(
        self, last_success, attempts_exhausted, timed_out
    ):
        # Total and pure: never raises on valid-typed input, identical inputs
        # yield identical outcomes (Req 11.7).
        first = resolve_outcome(last_success, attempts_exhausted, timed_out)
        second = resolve_outcome(last_success, attempts_exhausted, timed_out)
        assert first == second
