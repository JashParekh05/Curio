"""Unit tests for the YouTubeProvider declared capabilities and identity.

The YouTube Content_Provider must declare exactly the three Provider_Capabilities
(search, fetch_metadata, fetch_transcript) and expose the stable Provider_Id
``youtube`` (Req 2.1). These are plain pytest example tests importing only the
modules under test so they run offline with no external service.

Validates: Requirements 2.1
"""
from __future__ import annotations

from app.providers.youtube_provider import YouTubeProvider
from app.services.content_provider import ProviderCapability


def test_declared_capabilities_are_exactly_the_three_operations() -> None:
    """YouTubeProvider declares search, fetch_metadata, and fetch_transcript."""
    provider = YouTubeProvider()

    assert provider.capabilities == frozenset(
        {
            ProviderCapability.SEARCH,
            ProviderCapability.FETCH_METADATA,
            ProviderCapability.FETCH_TRANSCRIPT,
        }
    )


def test_provider_id_is_youtube() -> None:
    """The stable Provider_Id is ``youtube`` (Req 2.1)."""
    assert YouTubeProvider().provider_id == "youtube"


def test_cost_policy_exposes_search_and_metadata_cost_units() -> None:
    """The Provider_Cost_Policy declares SEARCH and FETCH_METADATA cost units."""
    cost_units = YouTubeProvider().cost_policy.cost_units

    assert ProviderCapability.SEARCH in cost_units
    assert ProviderCapability.FETCH_METADATA in cost_units
    assert cost_units[ProviderCapability.SEARCH] == 100
    assert cost_units[ProviderCapability.FETCH_METADATA] == 1
