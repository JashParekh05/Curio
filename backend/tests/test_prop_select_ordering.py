"""Property-based test for Source_Selection deterministic ordering.

# Feature: alternative-content-streams, Property 9: Source_Selection produces a total, deterministic, tie-broken order

``select_sources`` returns the eligible Content_Providers in a total,
deterministic order with ties broken by ascending Provider_Id, so identical
inputs always yield the same order (Req 6.6). Because it is a pure function of
the Provider_Registry snapshot and the availability map, the order depends only
on those inputs -- never on the order records happen to be registered in, on
invocation count, or on wall-clock time (Req 6.5).

This test asserts three things across generated registries (in varied record
orders) and availability maps engineered so that two or more providers are
eligible:
  - ``result.order`` is sorted strictly ascending by Provider_Id, which is the
    total deterministic, tie-broken order (Req 6.6);
  - calling ``select_sources`` twice on the same inputs yields an identical
    order (deterministic, Req 6.5);
  - building the registry from the SAME records in a SHUFFLED order yields the
    same order, proving the output is independent of record/registration order
    (Req 6.5).

Imports only the pure modules under test so it runs offline with no external
service (evidence for Req 9.5).

Validates: Requirements 6.5, 6.6
"""
from hypothesis import given, settings, strategies as st

from app.services.content_provider import ProviderCapability
from app.services.provider_registry import (
    ProviderCostPolicy,
    ProviderRecord,
    Registry,
)
from app.services.source_selection import (
    ProviderAvailability,
    select_sources,
)

# A fixed pool of Provider_Ids keeps registry ids unique while spanning a range
# of lexicographic orderings so the ascending tie-break is meaningfully tested.
_ID_POOL = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta"]


@st.composite
def _scenarios(draw):
    """Generate a (records, availability) pair with >= 2 eligible providers.

    Every generated provider is enabled, declares ``search``, is available, and
    has a remaining budget at or above its search Cost_Unit, so it is eligible by
    construction. A minimum of two providers guarantees the ordering / tie-break
    is exercised. Records are returned as a list so callers can place them into
    registries in different orders.
    """
    ids = draw(st.lists(st.sampled_from(_ID_POOL), unique=True, min_size=2, max_size=8))

    records = []
    availability = {}
    for pid in ids:
        search_cost = draw(st.integers(min_value=0, max_value=200))
        cost_units = {ProviderCapability.SEARCH: search_cost}
        capabilities = {ProviderCapability.SEARCH}
        # Optionally declare extra capabilities; irrelevant to ordering.
        if draw(st.booleans()):
            capabilities.add(ProviderCapability.FETCH_METADATA)
        if draw(st.booleans()):
            capabilities.add(ProviderCapability.FETCH_TRANSCRIPT)

        records.append(
            ProviderRecord(
                provider_id=pid,
                enabled=True,
                capabilities=frozenset(capabilities),
                cost_policy=ProviderCostPolicy(
                    cost_units=cost_units,
                    spend_budget=draw(st.integers(min_value=0, max_value=500)),
                ),
            )
        )

        # Remaining budget at or above the search cost keeps the provider
        # affordable, hence eligible.
        availability[pid] = ProviderAvailability(
            provider_id=pid,
            remaining_budget=draw(st.integers(min_value=search_cost, max_value=search_cost + 300)),
            available=True,
        )

    # Present the records in a freely-permuted order so the registry built from
    # them does not trivially arrive pre-sorted.
    permutation = draw(st.permutations(records))
    return list(permutation), availability


class TestSelectionOrdering:
    @settings(max_examples=100)
    @given(scenario=_scenarios())
    def test_order_is_ascending_by_provider_id(self, scenario):
        records, availability = scenario
        result = select_sources(Registry(records=tuple(records)), availability)

        # All generated providers are eligible, so every id appears exactly once.
        assert set(result.order) == {r.provider_id for r in records}
        # The order is the total, deterministic, tie-broken order: strictly
        # ascending by Provider_Id (Req 6.6).
        assert result.order == sorted(result.order)
        assert len(result.order) == len(set(result.order))

    @settings(max_examples=100)
    @given(scenario=_scenarios())
    def test_repeated_calls_yield_identical_order(self, scenario):
        records, availability = scenario
        registry = Registry(records=tuple(records))

        first = select_sources(registry, availability)
        second = select_sources(registry, availability)

        # Determinism: identical inputs always yield the same order (Req 6.5).
        assert first.order == second.order
        assert first.defer_reason == second.defer_reason

    @settings(max_examples=100)
    @given(scenario=_scenarios(), seed=st.randoms(use_true_random=False))
    def test_order_independent_of_record_order(self, scenario, seed):
        records, availability = scenario

        shuffled = list(records)
        seed.shuffle(shuffled)

        original = select_sources(Registry(records=tuple(records)), availability)
        reordered = select_sources(Registry(records=tuple(shuffled)), availability)

        # The same records in a shuffled registration order produce the same
        # order: output depends only on the inputs, not on record order or
        # wall-clock (Req 6.5).
        assert original.order == reordered.order
        assert original.defer_reason == reordered.defer_reason
