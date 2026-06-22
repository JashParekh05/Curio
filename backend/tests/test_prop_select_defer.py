"""Property-based test for Source_Selection deferral classification.

# Feature: alternative-content-streams, Property 10: Source_Selection deferral classification

When no Content_Provider is eligible, ``select_sources`` returns an empty
``order`` and a ``defer_reason`` that classifies *why* ingestion was deferred:

  - ``'no_enabled_provider'`` when zero providers are enabled (Req 3.5);
  - ``'no_search_capable_provider'`` when one or more are enabled but none
    declares the ``search`` Provider_Capability (Req 6.7);
  - ``'insufficient_provider_budget'`` when search-capable providers exist but
    none is eligible to afford a search -- whether unaffordable or unavailable
    (Req 6.3).

This test constructs three scenario families with Hypothesis -- one per deferral
reason -- and asserts that each yields an empty ``order`` paired with the matching
``defer_reason``.

Imports only the pure modules under test so it runs offline with no external
service (evidence for Req 9.5).

Validates: Requirements 3.5, 6.3, 6.7
"""
from hypothesis import given, settings, strategies as st

from app.services.content_provider import ProviderCapability
from app.services.provider_registry import (
    ProviderCostPolicy,
    ProviderRecord,
    Registry,
)
from app.services.source_selection import (
    INSUFFICIENT_PROVIDER_BUDGET,
    NO_ENABLED_PROVIDER,
    NO_SEARCH_CAPABLE_PROVIDER,
    ProviderAvailability,
    select_sources,
)

# A small fixed pool of Provider_Ids keeps registry ids unique.
_ID_POOL = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"]

# Capability subsets that do NOT contain SEARCH (used for family (b)).
_non_search_caps = st.sets(
    st.sampled_from(
        [ProviderCapability.FETCH_METADATA, ProviderCapability.FETCH_TRANSCRIPT]
    )
).map(frozenset)


def _cost_policy(search_cost, budget):
    return ProviderCostPolicy(
        cost_units={ProviderCapability.SEARCH: search_cost},
        spend_budget=budget,
    )


# ---------------------------------------------------------------------------
# Family (a): zero enabled providers -> 'no_enabled_provider'
# ---------------------------------------------------------------------------

@st.composite
def _zero_enabled_scenarios(draw):
    """A registry with zero enabled providers (possibly empty).

    Every record is disabled, so ``enabled_records`` is empty regardless of the
    records' capabilities, budgets, or availability. Disabled providers may still
    declare ``search`` and be affordable -- that must not change the outcome.
    """
    ids = draw(st.lists(st.sampled_from(_ID_POOL), unique=True, max_size=6))
    records = []
    availability = {}
    for pid in ids:
        caps = draw(st.sets(st.sampled_from(list(ProviderCapability))).map(frozenset))
        search_cost = draw(st.integers(min_value=0, max_value=200))
        records.append(
            ProviderRecord(
                provider_id=pid,
                enabled=False,  # the defining constraint of this family
                capabilities=caps,
                cost_policy=_cost_policy(search_cost, draw(st.integers(0, 500))),
            )
        )
        # Availability may be present and generous; it is irrelevant when disabled.
        if draw(st.booleans()):
            availability[pid] = ProviderAvailability(
                provider_id=pid,
                remaining_budget=draw(st.integers(0, 500)),
                available=draw(st.booleans()),
            )
    return Registry(records=tuple(records)), availability


# ---------------------------------------------------------------------------
# Family (b): >=1 enabled but none declares SEARCH -> 'no_search_capable_provider'
# ---------------------------------------------------------------------------

@st.composite
def _no_search_capable_scenarios(draw):
    """At least one enabled provider, none of which declares ``search``.

    Enabled records draw their capabilities from the SEARCH-free subset. Disabled
    records may freely declare ``search`` -- they are not considered, so they must
    not rescue the selection from the deferral.
    """
    enabled_ids = draw(
        st.lists(st.sampled_from(_ID_POOL), unique=True, min_size=1, max_size=4)
    )
    remaining_ids = [pid for pid in _ID_POOL if pid not in enabled_ids]
    disabled_ids = draw(
        st.lists(st.sampled_from(remaining_ids or ["__none__"]), unique=True, max_size=2)
    ) if remaining_ids else []

    records = []
    availability = {}
    for pid in enabled_ids:
        search_cost = draw(st.integers(min_value=0, max_value=200))
        records.append(
            ProviderRecord(
                provider_id=pid,
                enabled=True,
                capabilities=draw(_non_search_caps),  # never contains SEARCH
                cost_policy=_cost_policy(search_cost, draw(st.integers(0, 500))),
            )
        )
        # Make these enabled providers look generous/available to prove the
        # deferral is driven purely by the missing search capability.
        availability[pid] = ProviderAvailability(
            provider_id=pid,
            remaining_budget=draw(st.integers(min_value=200, max_value=500)),
            available=True,
        )

    for pid in disabled_ids:
        records.append(
            ProviderRecord(
                provider_id=pid,
                enabled=False,
                # Disabled providers may declare SEARCH; they are never considered.
                capabilities=frozenset({ProviderCapability.SEARCH}),
                cost_policy=_cost_policy(0, 500),
            )
        )
        availability[pid] = ProviderAvailability(
            provider_id=pid, remaining_budget=500, available=True
        )

    return Registry(records=tuple(records)), availability


# ---------------------------------------------------------------------------
# Family (c): search-capable exist but none affordable/available
#             -> 'insufficient_provider_budget'
# ---------------------------------------------------------------------------

@st.composite
def _insufficient_budget_scenarios(draw):
    """At least one enabled, search-capable provider, none of which is eligible.

    Each enabled, search-capable provider is made ineligible by exactly one of:
    being unavailable, being absent from the availability map, or having
    ``remaining_budget`` strictly below its search Cost_Unit. The search Cost_Unit
    is at least 1 so a below-budget remaining value exists.
    """
    ids = draw(
        st.lists(st.sampled_from(_ID_POOL), unique=True, min_size=1, max_size=4)
    )
    records = []
    availability = {}
    for pid in ids:
        search_cost = draw(st.integers(min_value=1, max_value=200))
        caps = draw(
            st.sets(
                st.sampled_from(
                    [ProviderCapability.FETCH_METADATA,
                     ProviderCapability.FETCH_TRANSCRIPT]
                )
            ).map(lambda s: frozenset(s | {ProviderCapability.SEARCH}))
        )
        records.append(
            ProviderRecord(
                provider_id=pid,
                enabled=True,
                capabilities=caps,  # always contains SEARCH
                cost_policy=_cost_policy(search_cost, draw(st.integers(0, 500))),
            )
        )
        # Choose one of three ways to make this provider ineligible.
        mode = draw(st.sampled_from(["unavailable", "absent", "underbudget"]))
        if mode == "unavailable":
            availability[pid] = ProviderAvailability(
                provider_id=pid,
                remaining_budget=draw(st.integers(min_value=0, max_value=500)),
                available=False,
            )
        elif mode == "underbudget":
            availability[pid] = ProviderAvailability(
                provider_id=pid,
                remaining_budget=draw(st.integers(min_value=0, max_value=search_cost - 1)),
                available=True,
            )
        # mode == "absent": leave pid out of the availability map entirely.
    return Registry(records=tuple(records)), availability


class TestSelectionDeferral:
    @settings(max_examples=100)
    @given(scenario=_zero_enabled_scenarios())
    def test_zero_enabled_defers_no_enabled_provider(self, scenario):
        registry, availability = scenario
        result = select_sources(registry, availability)
        assert result.order == []
        assert result.defer_reason == NO_ENABLED_PROVIDER

    @settings(max_examples=100)
    @given(scenario=_no_search_capable_scenarios())
    def test_enabled_without_search_defers_no_search_capable(self, scenario):
        registry, availability = scenario
        result = select_sources(registry, availability)
        assert result.order == []
        assert result.defer_reason == NO_SEARCH_CAPABLE_PROVIDER

    @settings(max_examples=100)
    @given(scenario=_insufficient_budget_scenarios())
    def test_search_capable_but_unaffordable_defers_insufficient_budget(self, scenario):
        registry, availability = scenario
        result = select_sources(registry, availability)
        assert result.order == []
        assert result.defer_reason == INSUFFICIENT_PROVIDER_BUDGET
