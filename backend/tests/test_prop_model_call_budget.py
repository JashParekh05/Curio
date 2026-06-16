"""Property-based test for the model-call budget cap.

# Feature: content-hook-engagement, Property 35: Model-call budget caps total calls per cycle

For any configured maximum and sequence of call requests, ModelCallBudget
permits at most clamp(max, 1, 100) calls and denies every request thereafter
within the cycle.

Validates: Requirements 8.9
"""
from hypothesis import given, settings, strategies as st

from app.services.coherence_budget import ModelCallBudget


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


class TestModelCallBudget:
    @settings(max_examples=100)
    @given(
        # Include values below the lower bound (<1) and above the upper bound
        # (>100) so the clamp behaviour is exercised on both ends.
        max_calls=st.integers(min_value=-50, max_value=200),
        requests=st.integers(min_value=0, max_value=250),
    )
    def test_budget_caps_total_calls_per_cycle(self, max_calls, requests):
        budget = ModelCallBudget(max_calls=max_calls)

        cap = _clamp(max_calls, 1, 100)
        assert budget.max_calls == cap

        results = [budget.allow() for _ in range(requests)]

        # Exactly min(requests, cap) calls are permitted.
        allowed = sum(1 for r in results if r)
        assert allowed == min(requests, cap)

        # Every request after the cap is reached returns False.
        for i, r in enumerate(results):
            assert r == (i < cap)

        # The counter never exceeds the cap.
        assert budget.used == min(requests, cap)
