"""Pure Source_Selection core for the Content_Provider abstraction.

Source_Selection is the decision of which enabled, search-capable, in-budget,
available Content_Providers to query for a Topic and in what order. It breaks the
single-source quota ceiling: ingestion is drawn from across every eligible
provider rather than being capped by any one source, and it continues when a
provider is exhausted or unavailable (Req 6).

This module is a single *pure* decision function over immutable value inputs --
no DB reads, no clock reads, no global state -- mirroring ``provider_registry.py``
/ ``provider_dedup.py`` / ``quota_pool.py``. Its output is fully determined by the
``Registry`` snapshot and the per-provider availability map supplied as inputs, so
identical inputs always yield an identical ``SelectionResult`` (Req 6.5). The thin
I/O shell that loads the registry and the live per-provider budgets/availability
lives elsewhere; this module never touches it. It satisfies Req 9.5 (pure,
unit-testable in isolation, no external services).

The rules it enforces:

  - A provider is eligible iff it is enabled in the Provider_Registry, declares
    the ``search`` Provider_Capability, is recorded as available, AND its
    remaining Spend_Budget is greater than or equal to the Cost_Unit its
    Provider_Cost_Policy declares for a search operation (Req 6.1, 6.2).
  - Eligible providers are returned in a total, deterministic order, ties broken
    by ascending Provider_Id, so identical inputs always yield the same order
    (Req 6.6).
  - When no provider is eligible the order is empty and a ``defer_reason``
    classifies why: ``'no_enabled_provider'`` (zero enabled, Req 3.5),
    ``'no_search_capable_provider'`` (one or more enabled but none declares
    ``search``, Req 6.7), or ``'insufficient_provider_budget'`` (search-capable
    providers exist but none is eligible to afford a search, Req 6.3).

ASCII only.

Validates: Requirements 3.2, 3.3, 3.5, 6.1, 6.2, 6.3, 6.5, 6.6, 6.7, 9.5
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from app.services.content_provider import ProviderCapability
from app.services.provider_registry import Registry, enabled_records


# ---------------------------------------------------------------------------
# Immutable value models (frozen)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProviderAvailability:
    """The live, per-provider inputs Source_Selection consumes for one provider.

    Attributes:
        provider_id: The Provider_Id this availability record describes.
        remaining_budget: The remaining Spend_Budget for the current accounting
            window. A provider is affordable only when this is greater than or
            equal to its search Cost_Unit (Req 6.1, 6.2).
        available: Whether the provider is recorded as available (not failed or
            unavailable). An unavailable provider is never eligible (Req 6.4).
    """

    provider_id: str
    remaining_budget: int
    available: bool


#: Why ingestion was deferred when no provider is eligible.
SelectionDefer = Literal[
    "no_enabled_provider",          # zero providers enabled (Req 3.5)
    "no_search_capable_provider",   # enabled exist but none declares search (Req 6.7)
    "insufficient_provider_budget",  # search-capable exist but none affordable (Req 6.3)
]

#: Zero Content_Providers are enabled in the Provider_Registry (Req 3.5).
NO_ENABLED_PROVIDER: SelectionDefer = "no_enabled_provider"

#: One or more providers are enabled but none declares ``search`` (Req 6.7).
NO_SEARCH_CAPABLE_PROVIDER: SelectionDefer = "no_search_capable_provider"

#: Search-capable providers exist but none can afford a search (Req 6.3).
INSUFFICIENT_PROVIDER_BUDGET: SelectionDefer = "insufficient_provider_budget"


@dataclass(frozen=True)
class SelectionResult:
    """The outcome of a Source_Selection decision.

    Attributes:
        order: The selected Provider_Ids in total, deterministic order (ties
            broken by ascending Provider_Id). Empty when no provider is eligible.
        defer_reason: The classification of why ``order`` is empty, or ``None``
            when at least one provider was selected.
    """

    order: list[str] = field(default_factory=list)
    defer_reason: SelectionDefer | None = None


# ---------------------------------------------------------------------------
# Pure core: the Source_Selection decision (Req 6.5, 9.5)
# ---------------------------------------------------------------------------

def select_sources(
    registry: Registry,
    availability: dict[str, ProviderAvailability],
) -> SelectionResult:
    """Choose which providers to query for a Topic and in what order.

    A provider is eligible iff it is enabled in the Provider_Registry, declares
    the ``search`` Provider_Capability, is recorded as available, AND its
    remaining Spend_Budget is greater than or equal to the Cost_Unit its
    Provider_Cost_Policy declares for a search operation (Req 6.1, 6.2). The
    eligible providers are returned in a total, deterministic order with ties
    broken by ascending Provider_Id, so identical inputs always yield the same
    order (Req 6.6).

    When no provider is eligible, ``order`` is empty and ``defer_reason``
    classifies why:

      - ``'no_enabled_provider'`` when zero providers are enabled (Req 3.5);
      - ``'no_search_capable_provider'`` when one or more are enabled but none
        declares ``search`` (Req 6.7);
      - ``'insufficient_provider_budget'`` when search-capable providers exist
        but none is eligible to afford a search -- whether unaffordable or
        unavailable (Req 6.3).

    Args:
        registry: The immutable Provider_Registry snapshot.
        availability: Per-provider live inputs keyed by Provider_Id. A provider
            absent from this map is treated as not available.

    Returns:
        A ``SelectionResult`` whose ``order`` lists the eligible Provider_Ids in
        ascending Provider_Id order, or an empty ``order`` with a ``defer_reason``
        when none is eligible.

    Pure and total: output depends only on the registry snapshot and the
    availability inputs, with no I/O, clock, or global state, producing identical
    output for identical inputs (Req 6.5).
    """
    enabled = enabled_records(registry)
    if not enabled:
        return SelectionResult(order=[], defer_reason=NO_ENABLED_PROVIDER)

    search_capable = [
        record
        for record in enabled
        if ProviderCapability.SEARCH in record.capabilities
    ]
    if not search_capable:
        return SelectionResult(order=[], defer_reason=NO_SEARCH_CAPABLE_PROVIDER)

    eligible: list[str] = []
    for record in search_capable:
        slot = availability.get(record.provider_id)
        if slot is None or not slot.available:
            continue
        search_cost = record.cost_policy.cost_units.get(ProviderCapability.SEARCH)
        if search_cost is None:
            continue
        if slot.remaining_budget >= search_cost:
            eligible.append(record.provider_id)

    if not eligible:
        return SelectionResult(
            order=[], defer_reason=INSUFFICIENT_PROVIDER_BUDGET
        )

    return SelectionResult(order=sorted(eligible), defer_reason=None)
