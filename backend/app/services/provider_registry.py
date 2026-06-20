"""Pure Provider_Registry core for the Content_Provider abstraction.

The Provider_Registry is the Operator-configured set of Content_Providers that
are registered for the Curio_System, together with each provider's declared
Provider_Capabilities and its Provider_Cost_Policy. This module models that set
as an immutable ``Registry`` snapshot and exposes the pure decision functions
that register a provider, toggle a provider's enabled state, and project the
enabled subset that source selection is allowed to query.

It follows the established ``app/services`` convention: a set of pure decision
functions (no DB, no clock, no globals -- deterministic in their inputs, as in
``provider_dedup.py`` / ``quota_pool.py``) over frozen, immutable dataclasses.
The thin I/O shell that loads the registry from Operator configuration (env or
the ``provider_registry`` table) and persists state changes lives elsewhere;
this module never touches it. These functions satisfy Req 9.5 (pure,
unit-testable in isolation, no external services).

The rules it enforces:

  - Each registered Content_Provider has a Provider_Id that is unique within the
    Provider_Registry (Req 1.7, 3.1).
  - Registering a Provider_Id already present is rejected, the existing entry is
    left unchanged, and ``'duplicate_provider_id'`` is returned (Req 1.8, 3.6).
  - Registering without a declared Provider_Capabilities value
    (``'missing_capabilities'``) or without a Provider_Cost_Policy value
    (``'missing_cost_policy'``) is rejected and creates no record (Req 3.7).
  - Toggling a provider's enabled/disabled state is visible to every subsequent
    selection with no code change (Req 3.8).
  - A disabled provider is never returned by ``enabled_records`` and so is never
    invoked (Req 3.2, 3.3).

ASCII only.

Validates: Requirements 1.7, 1.8, 3.2, 3.3, 3.6, 3.7, 3.8, 9.5
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.content_provider import ProviderCapability


# ---------------------------------------------------------------------------
# Immutable value models (frozen)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProviderCostPolicy:
    """The per-provider Provider_Cost_Policy (Req 3.1).

    Declares how each operation type is charged and the bound on spend for the
    current accounting window.

    Attributes:
        cost_units: The Cost_Unit charged per operation type. For YouTube a
            search costs 100 units and a metadata call 1 unit; other providers
            declare their own request-rate or cost limits.
        spend_budget: The Spend_Budget a provider may consume within its current
            accounting window before further calls are deferred.
    """

    cost_units: dict[ProviderCapability, int]
    spend_budget: int


@dataclass(frozen=True)
class ProviderRecord:
    """One Content_Provider's record in the Provider_Registry (Req 3.1).

    Attributes:
        provider_id: The stable Provider_Id, unique within the registry
            (Req 1.7, 3.1).
        enabled: Whether the provider is enabled or disabled. Only enabled
            providers are considered for selection (Req 3.2, 3.3).
        capabilities: The provider's declared Provider_Capabilities.
        cost_policy: The provider's Provider_Cost_Policy.
    """

    provider_id: str
    enabled: bool
    capabilities: frozenset[ProviderCapability]
    cost_policy: ProviderCostPolicy


@dataclass(frozen=True)
class Registry:
    """An immutable snapshot of the Operator-configured provider set.

    Attributes:
        records: The registered Content_Provider records, in registration order.
    """

    records: tuple[ProviderRecord, ...] = ()


# ---------------------------------------------------------------------------
# Error labels returned by ``register``
# ---------------------------------------------------------------------------

#: A record with the same Provider_Id already exists (Req 1.8, 3.6).
DUPLICATE_PROVIDER_ID: str = "duplicate_provider_id"

#: The candidate record declares no Provider_Capabilities (Req 3.7).
MISSING_CAPABILITIES: str = "missing_capabilities"

#: The candidate record has no Provider_Cost_Policy (Req 3.7).
MISSING_COST_POLICY: str = "missing_cost_policy"


# ---------------------------------------------------------------------------
# Pure core: registration decisions (Req 1.7, 1.8, 3.6, 3.7, 9.5)
# ---------------------------------------------------------------------------

def register(
    registry: Registry,
    record: ProviderRecord,
) -> tuple[Registry, str | None]:
    """Register ``record`` into ``registry``.

    Returns ``(new_registry, None)`` on success, where ``new_registry`` is a new
    ``Registry`` with ``record`` appended after the existing records. On
    rejection the UNCHANGED input registry is returned together with a specific
    error label and no record is created:

      - ``'duplicate_provider_id'`` when a record with the same Provider_Id is
        already present; the existing entry is left unchanged (Req 1.8, 3.6).
      - ``'missing_capabilities'`` when ``record.capabilities`` is empty
        (Req 3.7).
      - ``'missing_cost_policy'`` when ``record.cost_policy`` is None (Req 3.7).

    Completeness is validated before uniqueness so a record that is both
    incomplete and colliding reports the missing field. Capabilities are checked
    before the cost policy.

    Args:
        registry: The current immutable registry snapshot.
        record: The candidate Content_Provider record to register.

    Returns:
        ``(new_registry, None)`` on success, else ``(registry, error_label)``
        with the input registry returned unchanged.

    Pure and total: returns a new Registry on success and never mutates the
    input. (Req 1.7, 1.8, 3.6, 3.7)
    """
    if not record.capabilities:
        return registry, MISSING_CAPABILITIES
    if record.cost_policy is None:
        return registry, MISSING_COST_POLICY
    for existing in registry.records:
        if existing.provider_id == record.provider_id:
            return registry, DUPLICATE_PROVIDER_ID
    return Registry(records=registry.records + (record,)), None


def set_enabled(
    registry: Registry,
    provider_id: str,
    enabled: bool,
) -> Registry:
    """Return a new Registry with ``provider_id``'s enabled state set to ``enabled``.

    The change is visible to every subsequent selection with no code change
    (Req 3.8). Records keep their registration order, and a provider_id that is
    not present leaves the registry effectively unchanged.

    Args:
        registry: The current immutable registry snapshot.
        provider_id: The Provider_Id whose state to toggle.
        enabled: The new enabled (True) or disabled (False) state.

    Returns:
        A new ``Registry`` reflecting the toggled state.

    Pure and total: returns a new Registry and never mutates the input.
    (Req 3.8)
    """
    updated = tuple(
        ProviderRecord(
            provider_id=existing.provider_id,
            enabled=enabled,
            capabilities=existing.capabilities,
            cost_policy=existing.cost_policy,
        )
        if existing.provider_id == provider_id
        else existing
        for existing in registry.records
    )
    return Registry(records=updated)


def enabled_records(registry: Registry) -> list[ProviderRecord]:
    """Return the enabled records, in registry order.

    A disabled provider is never returned, so it is never invoked (Req 3.2,
    3.3).

    Args:
        registry: The current immutable registry snapshot.

    Returns:
        The subset of records whose state is enabled, preserving registry order.

    Pure and total: depends only on its input, performs no I/O. (Req 3.2, 3.3)
    """
    return [record for record in registry.records if record.enabled]
