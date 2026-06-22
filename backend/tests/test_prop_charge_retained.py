"""Property-based test for charge retention and free cache hits.

# Feature: alternative-content-streams, Property 13: Charge is retained when the operation fails; cache hits cost nothing

Two cost-accounting invariants are asserted here:

  - **Charge retention (Req 7.7).** When a billable operation's ``Cost_Unit`` has
    been charged against a provider's ``Spend_Budget`` and the operation then
    fails, the charge is retained and never credited back. ``charge_decision`` is
    a pure verify-then-charge step: an allowed charge yields
    ``new_spent == spent + cost_unit`` and the module exposes NO function that
    decrements spend. A subsequent operation failure is modeled by simply not
    persisting anything further -- there is no credit-back path -- so the spend a
    caller would have persisted (``spent + cost_unit``) is exactly what stays.

  - **Free cache hits (Req 7.6).** A requested operation served from an unexpired
    cache is not initiated and charges nothing: ``is_cached_free(True)`` is
    ``True`` and ``is_cached_free(False)`` is ``False``.

Imports only the pure module under test so it runs offline with no external
service (evidence for Req 9.5).

Validates: Requirements 7.6, 7.7
"""
import app.services.provider_cost as provider_cost
from hypothesis import given, settings, strategies as st

from app.services.provider_cost import (
    REASON_OK,
    SpendState,
    charge_decision,
    is_cached_free,
)


@st.composite
def _affordable_state_and_cost(draw):
    """An affordable SpendState (non-negative spent/budget) and a cost it can fund.

    The cost is drawn within the remaining headroom (``budget - spent``) so the
    charge is always allowed, covering the zero-cost and exactly-at-budget edges.
    """
    spent = draw(st.integers(min_value=0, max_value=10_000))
    headroom = draw(st.integers(min_value=0, max_value=10_000))
    budget = spent + headroom
    # Any cost in [0, headroom] is affordable; include both boundaries.
    cost_unit = draw(st.integers(min_value=0, max_value=headroom))
    return SpendState(provider_id="vimeo", spent=spent, budget=budget), cost_unit


class TestChargeRetained:
    @settings(max_examples=100)
    @given(data=_affordable_state_and_cost())
    def test_allowed_charge_is_retained_after_failure(self, data):
        state, cost_unit = data

        # The charge is verified and applied BEFORE the operation is initiated.
        decision = charge_decision(state, cost_unit)
        assert decision.allowed is True
        assert decision.reason == REASON_OK

        # The spend a caller persists before the call.
        charged_spent = decision.new_spent
        assert charged_spent == state.spent + cost_unit

        # Model the operation then FAILING. The pure core has no credit-back path:
        # there is no function that decrements spend, so the post-failure spend is
        # exactly the charged spend -- never reduced (Req 7.7).
        assert not hasattr(provider_cost, "credit_back")
        assert not hasattr(provider_cost, "refund")

        # Re-deriving the decision after a modeled failure yields the SAME charge:
        # the charge is retained, not reversed.
        post_failure = charge_decision(state, cost_unit)
        assert post_failure.new_spent == charged_spent
        assert post_failure.new_spent >= state.spent
        assert post_failure.new_spent == state.spent + cost_unit


class TestCacheHitsAreFree:
    def test_cache_hit_is_free(self):
        # An unexpired cache hit: the operation is served from cache and costs
        # nothing, so it is not initiated and no Cost_Unit is charged (Req 7.6).
        assert is_cached_free(True) is True

    def test_cache_miss_is_not_free(self):
        # A cache miss: the operation must be initiated, so it is not free.
        assert is_cached_free(False) is False

    @settings(max_examples=100)
    @given(cache_hit=st.booleans())
    def test_cached_free_iff_cache_hit(self, cache_hit):
        # is_cached_free is True exactly on a cache hit, for every input.
        assert is_cached_free(cache_hit) is cache_hit
