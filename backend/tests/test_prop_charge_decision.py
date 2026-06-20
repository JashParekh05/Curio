"""Property-based test for the charge-before-call decision.

# Feature: alternative-content-streams, Property 11: Charge-before-call decision

``charge_decision`` is the single atomic verify-then-charge step applied before
any billable operation is initiated (Req 7.1). For a readable ``SpendState`` and
a non-negative ``Cost_Unit`` the affordability rule is exact:

  - WHEN ``spent + cost_unit <= budget``  -> ``allowed=True``,
    ``new_spent == spent + cost_unit``, ``reason == 'ok'``.
  - OTHERWISE                             -> ``allowed=False``,
    ``reason == 'insufficient_budget'``, and the spend counters are left
    unchanged (``new_spent == state.spent``, Req 7.3).

This test generates ``SpendState``s with non-negative ``spent`` / ``budget`` and
non-negative cost units, deliberately covering the exactly-at-budget boundary
(``spent + cost == budget``, must be allowed) and the just-over-budget boundary
(``spent + cost == budget + 1``, must be refused with counters unchanged).

Imports only the pure module under test so it runs offline with no external
service (evidence for Req 9.5).

Validates: Requirements 7.1, 7.3
"""
from hypothesis import given, settings, strategies as st

from app.services.provider_cost import (
    REASON_INSUFFICIENT_BUDGET,
    REASON_OK,
    ChargeDecision,
    SpendState,
    charge_decision,
)


@st.composite
def _state_and_cost(draw):
    """A SpendState (non-negative spent/budget) and a non-negative cost_unit.

    The cost is drawn relative to the remaining headroom so the exactly-at-budget
    and just-over-budget boundaries are hit often, alongside generic affordable
    and over-budget cases.
    """
    spent = draw(st.integers(min_value=0, max_value=10_000))
    budget = draw(st.integers(min_value=0, max_value=10_000))
    remaining = budget - spent  # may be negative when already over budget

    # Bias toward the boundaries: exactly-at-budget and just-over-budget.
    cost_unit = draw(
        st.one_of(
            st.just(max(0, remaining)),       # spent + cost == budget (allowed edge)
            st.just(max(0, remaining) + 1),   # spent + cost == budget + 1 (refused edge)
            st.integers(min_value=0, max_value=10_000),  # generic non-negative cost
        )
    )
    return SpendState(provider_id="vimeo", spent=spent, budget=budget), cost_unit


class TestChargeDecision:
    @settings(max_examples=100)
    @given(data=_state_and_cost())
    def test_decision_tracks_affordability_rule(self, data):
        state, cost_unit = data
        decision = charge_decision(state, cost_unit)

        assert isinstance(decision, ChargeDecision)

        if state.spent + cost_unit <= state.budget:
            # Affordable: charge allowed, spend advances by exactly the cost.
            assert decision.allowed is True
            assert decision.new_spent == state.spent + cost_unit
            assert decision.reason == REASON_OK
        else:
            # Over budget: refused, counters unchanged.
            assert decision.allowed is False
            assert decision.reason == REASON_INSUFFICIENT_BUDGET
            assert decision.new_spent == state.spent

    @settings(max_examples=100)
    @given(
        spent=st.integers(min_value=0, max_value=10_000),
        budget=st.integers(min_value=0, max_value=10_000),
    )
    def test_exactly_at_budget_is_allowed(self, spent, budget):
        # The exactly-at-budget boundary (spent + cost == budget) must be allowed
        # only when there is non-negative headroom to consume.
        state = SpendState(provider_id="podcast", spent=spent, budget=budget)
        remaining = budget - spent
        if remaining < 0:
            return  # already over budget; not the at-budget boundary
        decision = charge_decision(state, remaining)
        assert decision.allowed is True
        assert decision.new_spent == budget
        assert decision.reason == REASON_OK

    @settings(max_examples=100)
    @given(
        spent=st.integers(min_value=0, max_value=10_000),
        budget=st.integers(min_value=0, max_value=10_000),
    )
    def test_just_over_budget_is_refused_unchanged(self, spent, budget):
        # The just-over-budget boundary (spent + cost == budget + 1) must be
        # refused with the spend counters left unchanged.
        state = SpendState(provider_id="khan_academy", spent=spent, budget=budget)
        remaining = budget - spent
        cost_unit = max(0, remaining) + 1
        decision = charge_decision(state, cost_unit)
        assert decision.allowed is False
        assert decision.reason == REASON_INSUFFICIENT_BUDGET
        assert decision.new_spent == state.spent
