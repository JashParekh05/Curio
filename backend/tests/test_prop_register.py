"""Property-based test for Provider_Registry registration.

# Feature: alternative-content-streams, Property 4: Registration enforces unique ids and complete records

``register`` adds a candidate ``ProviderRecord`` to an immutable ``Registry``
only when the record is complete (declares a non-empty Provider_Capabilities set
AND carries a Provider_Cost_Policy) and its Provider_Id is unique within the
registry (Req 1.7, 1.8, 3.6, 3.7). On rejection it returns the UNCHANGED input
registry paired with the specific error label and creates no record.

The implementation checks completeness BEFORE uniqueness, and capabilities before
the cost policy, so a record that is both incomplete and colliding reports the
missing field. This test asserts that exact precedence:

  - empty capabilities                -> 'missing_capabilities'
  - else None cost policy             -> 'missing_cost_policy'
  - else colliding Provider_Id        -> 'duplicate_provider_id'
  - else success: a NEW registry whose Provider_Ids are all unique, with the
    record appended, and ``None`` error.

Imports only the pure module under test so it runs offline with no external
service (evidence for Req 9.5).

Validates: Requirements 1.7, 1.8, 3.6, 3.7
"""
from hypothesis import given, settings, strategies as st

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

# A small id pool so candidate ids collide with existing records often enough
# to exercise the duplicate path, while still leaving room for unique ids.
_ids = st.sampled_from(["youtube", "vimeo", "podcast", "khan_academy", "spotify"])

# Capability sets including the empty set (which triggers 'missing_capabilities').
_capabilities = st.sets(st.sampled_from(list(ProviderCapability))).map(frozenset)


@st.composite
def _cost_policies(draw):
    """A Provider_Cost_Policy with a per-op Cost_Unit map and a Spend_Budget."""
    cost_units = draw(
        st.dictionaries(
            st.sampled_from(list(ProviderCapability)),
            st.integers(min_value=0, max_value=100),
            max_size=3,
        )
    )
    spend_budget = draw(st.integers(min_value=0, max_value=10_000))
    return ProviderCostPolicy(cost_units=cost_units, spend_budget=spend_budget)


@st.composite
def _records(draw, *, provider_id=None):
    """A candidate ProviderRecord with varied capabilities and cost policy.

    capabilities vary between empty and non-empty, and cost_policy varies between
    None and present, so every rejection branch and the success branch are
    reachable.
    """
    pid = provider_id if provider_id is not None else draw(_ids)
    return ProviderRecord(
        provider_id=pid,
        enabled=draw(st.booleans()),
        capabilities=draw(_capabilities),
        cost_policy=draw(st.one_of(st.none(), _cost_policies())),
    )


@st.composite
def _registries(draw):
    """A Registry of complete records with UNIQUE Provider_Ids (a valid snapshot)."""
    pids = draw(st.lists(_ids, max_size=4, unique=True))
    records = tuple(
        ProviderRecord(
            provider_id=pid,
            enabled=draw(st.booleans()),
            # Existing records are complete: non-empty caps + a cost policy.
            capabilities=draw(
                st.sets(st.sampled_from(list(ProviderCapability)), min_size=1).map(
                    frozenset
                )
            ),
            cost_policy=draw(_cost_policies()),
        )
        for pid in pids
    )
    return Registry(records=records)


def _expected_error(registry, record):
    """The error label the precedence (completeness before uniqueness) dictates."""
    if not record.capabilities:
        return MISSING_CAPABILITIES
    if record.cost_policy is None:
        return MISSING_COST_POLICY
    if any(r.provider_id == record.provider_id for r in registry.records):
        return DUPLICATE_PROVIDER_ID
    return None


class TestRegister:
    @settings(max_examples=100)
    @given(registry=_registries(), record=_records())
    def test_register_matches_completeness_then_uniqueness_rule(self, registry, record):
        new_registry, error = register(registry, record)
        expected = _expected_error(registry, record)

        # The returned error label matches the implementation's precedence:
        # capabilities -> cost policy -> uniqueness.
        assert error == expected

        if expected is None:
            # Success: a new registry with the record appended.
            assert new_registry is not registry
            assert new_registry.records == registry.records + (record,)
            # All Provider_Ids in the resulting registry are unique.
            pids = [r.provider_id for r in new_registry.records]
            assert len(pids) == len(set(pids))
        else:
            # Rejection returns the UNCHANGED input registry.
            assert new_registry is registry
            assert new_registry.records == registry.records

    @settings(max_examples=100)
    @given(registry=_registries(), record=_records())
    def test_register_never_mutates_input(self, registry, record):
        before = registry.records
        register(registry, record)
        # The input snapshot is never mutated regardless of outcome.
        assert registry.records == before
