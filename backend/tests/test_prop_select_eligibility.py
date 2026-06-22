"""Property-based test for Source_Selection eligibility.

# Feature: alternative-content-streams, Property 8: Source_Selection eligibility

``select_sources`` picks which Content_Providers to query for a Topic. A provider
is eligible iff it is enabled in the Provider_Registry, declares the ``search``
Provider_Capability, is recorded as available, AND its remaining Spend_Budget is
greater than or equal to the Cost_Unit its Provider_Cost_Policy declares for a
search operation (Req 3.2, 3.3, 6.1, 6.2).

This test asserts two things across generated registries and availability maps:
  - the selected set ``set(result.order)`` is exactly the providers that are
    enabled AND declare ``search`` AND are available AND have
    ``remaining_budget >= search Cost_Unit``;
  - toggling a single provider's enabled state via ``provider_registry.set_enabled``
    flips that provider's presence in the selection accordingly, with no other
    input changed (Req 3.8).

Imports only the pure modules under test so it runs offline with no external
service (evidence for Req 9.5).

Validates: Requirements 3.2, 3.3, 3.8, 6.1, 6.2
"""
from hypothesis import assume, given, settings, strategies as st

from app.services.content_provider import ProviderCapability
from app.services.provider_registry import (
    ProviderCostPolicy,
    ProviderRecord,
    Registry,
    set_enabled,
)
from app.services.source_selection import (
    ProviderAvailability,
    select_sources,
)

# A small fixed pool of Provider_Ids keeps registry ids unique while still
# letting availability keys collide with (or be absent from) registry ids.
_ID_POOL = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"]

# A capability set is any subset of the declared capabilities, including the
# empty set and sets that may or may not contain SEARCH.
_capability_sets = st.sets(st.sampled_from(list(ProviderCapability))).map(frozenset)


def _expected_selected(registry, availability):
    """Independent oracle for the eligible Provider_Id set.

    Mirrors the eligibility rule directly from the acceptance criteria: enabled
    AND declares search AND available AND remaining_budget >= search Cost_Unit.
    """
    selected = set()
    for record in registry.records:
        if not record.enabled:
            continue
        if ProviderCapability.SEARCH not in record.capabilities:
            continue
        slot = availability.get(record.provider_id)
        if slot is None or not slot.available:
            continue
        search_cost = record.cost_policy.cost_units.get(ProviderCapability.SEARCH)
        if search_cost is None:
            continue
        if slot.remaining_budget >= search_cost:
            selected.add(record.provider_id)
    return selected


@st.composite
def _scenarios(draw):
    """Generate a (registry, availability) pair with varied eligibility inputs."""
    ids = draw(st.lists(st.sampled_from(_ID_POOL), unique=True, max_size=6))

    records = []
    availability = {}
    for pid in ids:
        enabled = draw(st.booleans())
        capabilities = draw(_capability_sets)
        # Every provider declares a search Cost_Unit in its cost policy; the
        # capability set independently decides whether it is search-capable.
        search_cost = draw(st.integers(min_value=0, max_value=200))
        cost_units = {ProviderCapability.SEARCH: search_cost}
        # Optionally also declare a metadata Cost_Unit (irrelevant to selection).
        if draw(st.booleans()):
            cost_units[ProviderCapability.FETCH_METADATA] = draw(
                st.integers(min_value=0, max_value=10)
            )
        records.append(
            ProviderRecord(
                provider_id=pid,
                enabled=enabled,
                capabilities=capabilities,
                cost_policy=ProviderCostPolicy(
                    cost_units=cost_units,
                    spend_budget=draw(st.integers(min_value=0, max_value=500)),
                ),
            )
        )

        # An availability record may be present or absent. When present, the
        # remaining budget is drawn at, below, and above the search cost so the
        # affordability boundary is exercised in both directions.
        if draw(st.booleans()):
            remaining = draw(
                st.one_of(
                    st.just(search_cost),          # exactly at budget
                    st.just(max(search_cost - 1, 0)),  # just below
                    st.just(search_cost + 1),      # just above
                    st.integers(min_value=0, max_value=300),
                )
            )
            availability[pid] = ProviderAvailability(
                provider_id=pid,
                remaining_budget=remaining,
                available=draw(st.booleans()),
            )

    return Registry(records=tuple(records)), availability


class TestSelectionEligibility:
    @settings(max_examples=100)
    @given(scenario=_scenarios())
    def test_selected_set_is_exactly_eligible_providers(self, scenario):
        registry, availability = scenario
        result = select_sources(registry, availability)
        # The selected set is exactly the enabled + search-capable + available +
        # affordable providers (Req 3.2, 3.3, 6.1, 6.2).
        assert set(result.order) == _expected_selected(registry, availability)

    @settings(max_examples=100)
    @given(scenario=_scenarios())
    def test_toggling_enabled_flips_presence(self, scenario):
        registry, availability = scenario
        assume(registry.records)

        # Pick one provider and flip only its enabled state.
        target = registry.records[0]
        toggled = set_enabled(registry, target.provider_id, not target.enabled)

        result = select_sources(toggled, availability)
        selected = set(result.order)

        # The full selection still matches the oracle after the toggle, proving
        # the toggle changed nothing else.
        assert selected == _expected_selected(toggled, availability)

        # Presence of the toggled provider tracks its NEW enabled state combined
        # with the unchanged search-capable + available + affordable criteria.
        slot = availability.get(target.provider_id)
        search_cost = target.cost_policy.cost_units.get(ProviderCapability.SEARCH)
        other_criteria = (
            ProviderCapability.SEARCH in target.capabilities
            and slot is not None
            and slot.available
            and search_cost is not None
            and slot.remaining_budget >= search_cost
        )
        assert (target.provider_id in selected) == (
            (not target.enabled) and other_criteria
        )
