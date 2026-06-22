"""Property-based test for bounded search results.

# Feature: alternative-content-streams, Property 1: Search results are bounded

``bound_results`` enforces the Content_Provider search contract's invariant that
a search never yields more than the configured maximum result count (Req 1.1).
For any candidate list and any non-negative ``max_results``, the returned list is
an order-preserving PREFIX of the input whose length never exceeds
``max(0, max_results)``.

Imports only the pure module under test so it runs offline with no external
service (evidence for Req 9.5).

Validates: Requirements 1.1
"""
from hypothesis import given, settings, strategies as st

from app.services.content_provider import bound_results

# bound_results only slices the input, so any list element type exercises it.
# Small ints are simple, cheap stand-ins for SourceItems for this property.
_items = st.lists(st.integers(min_value=-50, max_value=50), max_size=30)


class TestBoundResults:
    @settings(max_examples=100)
    @given(
        items=_items,
        # Non-negative max_results, including 0 and values above/below len(items).
        max_results=st.integers(min_value=0, max_value=40),
    )
    def test_result_is_length_bounded_order_preserving_prefix(
        self, items, max_results
    ):
        result = bound_results(items, max_results)

        # Length is bounded by max(0, max_results) (Req 1.1).
        assert len(result) <= max(0, max_results)

        # Result is an order-preserving prefix of the input.
        assert result == items[: len(result)]

    @settings(max_examples=100)
    @given(items=_items)
    def test_zero_max_results_yields_empty(self, items):
        # A max of 0 admits nothing regardless of how many candidates exist.
        assert bound_results(items, 0) == []

    @settings(max_examples=100)
    @given(items=_items, max_results=st.integers(min_value=0, max_value=40))
    def test_returns_full_input_when_bound_at_or_above_length(
        self, items, max_results
    ):
        # When the bound is at or above the candidate count, every item in
        # original order is returned (a prefix equal to the whole list).
        if max_results >= len(items):
            assert bound_results(items, max_results) == items
