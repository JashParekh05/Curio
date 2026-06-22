"""Concrete unit tests for Provider_Registry ``register``.

# Feature: alternative-content-streams

Plain pytest example-based tests pinning the four concrete outcomes of the pure
``register`` decision (the property test ``test_prop_register.py`` covers the
universal rule across generated inputs; these lock the specific cases down):

  - empty capabilities                 -> 'missing_capabilities'
  - None cost policy                   -> 'missing_cost_policy'
  - colliding Provider_Id              -> 'duplicate_provider_id' (existing
                                          entry left unchanged)
  - a valid, unique, complete record   -> success: a NEW Registry with the
                                          record appended and None error.

Imports only the pure module under test (plus the ``ProviderCapability`` enum the
record requires) so it runs offline with no external service (evidence for
Req 9.5).

Validates: Requirements 1.8, 3.6, 3.7
"""
from app.services.content_provider import ProviderCapability
from app.services.provider_registry import (
    DUPLICATE_PROVIDER_ID,
    MISSING_CAPABILITIES,
    MISSING_COST_POLICY,
    ProviderCostPolicy,
    ProviderRecord,
    Registry,
    register,
)


def _cost_policy() -> ProviderCostPolicy:
    """A complete, valid Provider_Cost_Policy."""
    return ProviderCostPolicy(
        cost_units={ProviderCapability.SEARCH: 100},
        spend_budget=10_000,
    )


def _record(
    provider_id: str = "vimeo",
    *,
    capabilities=frozenset({ProviderCapability.SEARCH}),
    cost_policy=None,
    enabled: bool = True,
) -> ProviderRecord:
    """A candidate record; defaults to a complete, valid one."""
    return ProviderRecord(
        provider_id=provider_id,
        enabled=enabled,
        capabilities=capabilities,
        cost_policy=cost_policy if cost_policy is not None else _cost_policy(),
    )


class TestRegisterConcreteCases:
    def test_empty_capabilities_rejected_unchanged(self):
        registry = Registry()
        record = _record(capabilities=frozenset())

        new_registry, error = register(registry, record)

        assert error == MISSING_CAPABILITIES
        # The registry is returned unchanged and no record is created.
        assert new_registry is registry
        assert new_registry.records == ()

    def test_none_cost_policy_rejected_unchanged(self):
        registry = Registry()
        # Build a record whose cost_policy is None directly (bypass the helper
        # default so the missing-policy branch is exercised).
        record = ProviderRecord(
            provider_id="vimeo",
            enabled=True,
            capabilities=frozenset({ProviderCapability.SEARCH}),
            cost_policy=None,
        )

        new_registry, error = register(registry, record)

        assert error == MISSING_COST_POLICY
        assert new_registry is registry
        assert new_registry.records == ()

    def test_colliding_provider_id_rejected_existing_untouched(self):
        existing = _record(
            provider_id="youtube",
            capabilities=frozenset({ProviderCapability.SEARCH}),
        )
        registry = Registry(records=(existing,))
        # A complete, valid record that collides on Provider_Id but differs in
        # every other field; the existing entry must remain untouched.
        colliding = _record(
            provider_id="youtube",
            capabilities=frozenset(
                {ProviderCapability.SEARCH, ProviderCapability.FETCH_TRANSCRIPT}
            ),
            enabled=False,
        )

        new_registry, error = register(registry, colliding)

        assert error == DUPLICATE_PROVIDER_ID
        # Unchanged registry returned; existing entry identical to before.
        assert new_registry is registry
        assert new_registry.records == (existing,)
        assert new_registry.records[0] is existing

    def test_valid_unique_record_appended_success(self):
        existing = _record(provider_id="youtube")
        registry = Registry(records=(existing,))
        record = _record(
            provider_id="vimeo",
            capabilities=frozenset({ProviderCapability.SEARCH}),
        )

        new_registry, error = register(registry, record)

        assert error is None
        # A NEW Registry with the record appended after the existing records.
        assert new_registry is not registry
        assert new_registry.records == (existing, record)
        # Input registry was not mutated.
        assert registry.records == (existing,)
