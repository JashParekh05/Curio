"""Concrete unit tests for Source_Selection deferral classification.

# Feature: alternative-content-streams

Plain pytest example-based tests pinning the three concrete deferral outcomes of
the pure ``select_sources`` decision (the property test
``test_prop_select_defer.py`` covers the universal rule across generated inputs;
these lock the specific cases down). When no Content_Provider is eligible,
``select_sources`` returns an empty ``order`` paired with the ``defer_reason``
that classifies *why*:

  - zero enabled providers (empty registry or all disabled)
        -> 'no_enabled_provider'
  - one enabled provider that does NOT declare ``search``
        -> 'no_search_capable_provider'
  - one enabled, search-capable provider whose remaining budget is below its
    search Cost_Unit (and one recorded unavailable)
        -> 'insufficient_provider_budget'

Imports only the modules under test so it runs offline with no external service
(evidence for Req 9.5).

Validates: Requirements 3.5, 6.3, 6.7
"""
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


def _cost_policy(search_cost: int = 100, budget: int = 10_000) -> ProviderCostPolicy:
    """A Provider_Cost_Policy declaring a search Cost_Unit."""
    return ProviderCostPolicy(
        cost_units={ProviderCapability.SEARCH: search_cost},
        spend_budget=budget,
    )


class TestZeroEnabledDeferral:
    def test_empty_registry_defers_no_enabled_provider(self):
        result = select_sources(Registry(), {})

        assert result.order == []
        assert result.defer_reason == NO_ENABLED_PROVIDER

    def test_all_disabled_defers_no_enabled_provider(self):
        # A disabled provider that would otherwise be perfectly eligible:
        # search-capable, affordable, and available. It must not rescue the
        # selection because it is disabled.
        disabled = ProviderRecord(
            provider_id="vimeo",
            enabled=False,
            capabilities=frozenset({ProviderCapability.SEARCH}),
            cost_policy=_cost_policy(search_cost=100, budget=10_000),
        )
        registry = Registry(records=(disabled,))
        availability = {
            "vimeo": ProviderAvailability(
                provider_id="vimeo", remaining_budget=10_000, available=True
            )
        }

        result = select_sources(registry, availability)

        assert result.order == []
        assert result.defer_reason == NO_ENABLED_PROVIDER


class TestNoSearchCapableDeferral:
    def test_enabled_without_search_defers_no_search_capable(self):
        # Enabled, available, and generously funded -- but it does not declare
        # the SEARCH capability, so it cannot be queried.
        no_search = ProviderRecord(
            provider_id="podcast",
            enabled=True,
            capabilities=frozenset(
                {ProviderCapability.FETCH_METADATA,
                 ProviderCapability.FETCH_TRANSCRIPT}
            ),
            cost_policy=_cost_policy(search_cost=100, budget=10_000),
        )
        registry = Registry(records=(no_search,))
        availability = {
            "podcast": ProviderAvailability(
                provider_id="podcast", remaining_budget=10_000, available=True
            )
        }

        result = select_sources(registry, availability)

        assert result.order == []
        assert result.defer_reason == NO_SEARCH_CAPABLE_PROVIDER


class TestInsufficientBudgetDeferral:
    def test_underbudget_and_unavailable_defer_insufficient_budget(self):
        # Two enabled, search-capable providers, neither eligible:
        #   - "vimeo": available but remaining_budget (50) < search cost (100)
        #   - "khan": funded but recorded unavailable
        underbudget = ProviderRecord(
            provider_id="vimeo",
            enabled=True,
            capabilities=frozenset({ProviderCapability.SEARCH}),
            cost_policy=_cost_policy(search_cost=100, budget=10_000),
        )
        unavailable = ProviderRecord(
            provider_id="khan",
            enabled=True,
            capabilities=frozenset({ProviderCapability.SEARCH}),
            cost_policy=_cost_policy(search_cost=100, budget=10_000),
        )
        registry = Registry(records=(underbudget, unavailable))
        availability = {
            "vimeo": ProviderAvailability(
                provider_id="vimeo", remaining_budget=50, available=True
            ),
            "khan": ProviderAvailability(
                provider_id="khan", remaining_budget=10_000, available=False
            ),
        }

        result = select_sources(registry, availability)

        assert result.order == []
        assert result.defer_reason == INSUFFICIENT_PROVIDER_BUDGET
