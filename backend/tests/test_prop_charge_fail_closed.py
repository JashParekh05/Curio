"""Property-based test for fail-closed cost accounting.

# Feature: alternative-content-streams, Property 12: Cost accounting fails closed

When a Content_Provider's Spend_Budget accounting record cannot be read or its
read times out, the accounting state is ``None``. ``charge_decision`` must treat
that provider as having no remaining budget and refuse the charge, so no billable
operation is ever initiated against a provider whose accounting is unavailable
(Req 7.4). This is the fail-closed invariant: an accounting outage halts spend
rather than risking overspend.

For every non-negative ``cost_unit``, ``charge_decision(None, cost_unit)`` returns
``allowed=False`` with ``reason='accounting_unavailable'`` and no spend to persist.

Imports only the pure module under test so it runs offline with no external
service (evidence for Req 9.5).

Validates: Requirements 7.4
"""
from hypothesis import given, settings, strategies as st

from app.services.provider_cost import (
    REASON_ACCOUNTING_UNAVAILABLE,
    charge_decision,
)


class TestChargeFailsClosed:
    @settings(max_examples=100)
    @given(
        # Non-negative Cost_Units, including 0 and large charges; the decision
        # must fail closed for every one of them.
        cost_unit=st.integers(min_value=0, max_value=10_000),
    )
    def test_none_state_fails_closed(self, cost_unit):
        decision = charge_decision(None, cost_unit)

        # No billable operation may be initiated when accounting is unavailable.
        assert decision.allowed is False
        # The outage is reported with the specific fail-closed reason (Req 7.4).
        assert decision.reason == REASON_ACCOUNTING_UNAVAILABLE
        # There is no accounting record to charge, so nothing is persisted.
        assert decision.new_spent == 0
