"""Property-based test for model-call budget enforcement monotonicity.

# Feature: deep-content-ingestion, Property 16: Model-call budget enforcement is monotonic

For any budget in [1, 100]:
  * ``within_model_budget(calls_made, budget)`` is True if and only if
    ``calls_made < budget``.
  * Enforcement is monotonic: once it returns False for some ``calls_made``, it
    stays False for every larger ``calls_made`` (the pipeline never re-opens a
    closed budget as more calls are issued).

Validates: Requirements 9.3
"""
from hypothesis import given, settings, strategies as st

from app.services.ingestion_budget import within_model_budget


# Budget constrained to its validated range [1, 100]; calls_made spans below,
# at, and beyond the budget so the True/False boundary is always exercised.
_budget_st = st.integers(min_value=1, max_value=100)
_calls_st = st.integers(min_value=0, max_value=200)


class TestModelBudgetMonotonic:
    @settings(max_examples=100)
    @given(budget=_budget_st, calls_made=_calls_st)
    def test_within_budget_iff_strictly_less(self, budget, calls_made):
        # True iff calls_made < budget.
        assert within_model_budget(calls_made, budget) == (calls_made < budget)

    @settings(max_examples=100)
    @given(budget=_budget_st, calls_made=_calls_st, extra=st.integers(min_value=0, max_value=200))
    def test_once_false_stays_false(self, budget, calls_made, extra):
        # Once the budget is exhausted, issuing more calls keeps it exhausted.
        if not within_model_budget(calls_made, budget):
            assert not within_model_budget(calls_made + extra, budget)
