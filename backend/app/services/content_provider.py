"""The uniform Content_Provider contract and its capability/identity pure cores.

This module defines the single abstraction the shared Ingestion_Pipeline depends
on so that source acquisition no longer references YouTube-specific calls
(Req 1.1, 1.2). Every content source (YouTube, Vimeo, podcast feeds, Khan
Academy, ...) implements the ``ContentProvider`` Protocol and normalizes its
output into the same embed-based ``SourceItem`` model.

It follows the established ``app/services`` convention: a set of pure decision
functions (no DB, no clock, no globals -- deterministic in their inputs, as in
``coherence_budget.py`` / ``quota_pool.py``) plus immutable, frozen dataclasses.
The decision functions here resolve provider capabilities, gate unsupported
invocations, validate returned-item provider identity, and bound search results.
They satisfy Req 9.5 (pure, unit-testable in isolation, no external services).

ASCII only.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Provider capabilities
# ---------------------------------------------------------------------------

class ProviderCapability(str, Enum):
    """A declared capability of a Content_Provider (Req 1.3).

    A provider enumerates which ingestion operations it actually supports; the
    pipeline invokes through a provider only the operations it declares here.
    Inheriting from ``str`` keeps the values JSON/text friendly so they round
    trip cleanly through the ``provider_registry.capabilities`` text[] column.
    """

    SEARCH = "search"
    FETCH_METADATA = "fetch_metadata"
    FETCH_TRANSCRIPT = "fetch_transcript"


# ---------------------------------------------------------------------------
# In-memory models (frozen, immutable)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EmbedReference:
    """The normalized, provider-tagged playback descriptor for a Source_Item.

    Consists of the producing provider's Provider_Id, the provider's external
    item identifier, and the playback parameters required to render the embed.
    An Embed_Reference contains no hosted media (Req 4.1).
    """

    provider_id: str
    external_id: str
    playback_params: dict[str, str]


@dataclass(frozen=True)
class SourceItem:
    """A single candidate piece of content selected for ingestion for a Topic.

    Generalizes the YouTube-only source video; identified by its Provider_Id and
    an Embed_Reference, and carrying the transcript and metadata consumed during
    ingestion. No media is downloaded (Req 4.1).
    """

    provider_id: str
    embed_ref: EmbedReference
    title: str
    duration_seconds: float | None
    transcript: list[dict] | None
    metadata: dict


# ---------------------------------------------------------------------------
# The Content_Provider contract
# ---------------------------------------------------------------------------

@runtime_checkable
class ContentProvider(Protocol):
    """The uniform contract the Ingestion_Pipeline depends on (Req 1.1, 1.2).

    Exactly three capability-gated operations are exposed: a ``search`` returning
    a bounded list of candidate Source_Items, a ``fetch_metadata`` for a single
    item, and a ``fetch_transcript`` for an item's time-aligned text track. A
    provider declares which of these it actually supports through
    ``capabilities``; the pipeline invokes only declared operations and never
    references any Provider_Id-specific interface.
    """

    @property
    def provider_id(self) -> str:
        """The stable Provider_Id, unique within the Provider_Registry."""
        ...

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        """The set of operations this provider declares it supports (Req 1.3)."""
        ...

    @property
    def cost_policy(self) -> "object":
        """The provider's Provider_Cost_Policy (see ``provider_registry``)."""
        ...

    def search(self, query: str, *, max_results: int) -> list[SourceItem]:
        """Return at most ``max_results`` candidate Source_Items (Req 1.1)."""
        ...

    def fetch_metadata(self, external_id: str) -> dict:
        """Return normalized metadata for the requested Source_Item."""
        ...

    def fetch_transcript(self, external_id: str) -> list[dict] | None:
        """Return the item's time-aligned transcript, or None when unavailable."""
        ...


# ---------------------------------------------------------------------------
# Pure core: capability resolution (Req 1.3, 1.4, 5.5, 9.5)
# ---------------------------------------------------------------------------

#: Error label returned by ``gate_invocation`` when an op is not declared.
UNSUPPORTED_OPERATION: str = "unsupported_operation"


def supports_capability(
    capabilities: frozenset[ProviderCapability],
    op: ProviderCapability,
) -> bool:
    """Return True iff ``op`` is among the provider's declared capabilities.

    The pipeline invokes through a provider only operations for which this
    returns True (Req 1.3).

    Args:
        capabilities: The provider's declared Provider_Capabilities.
        op: The operation the pipeline wants to invoke.

    Returns:
        True if and only if ``op`` is in ``capabilities``.

    Pure and total: depends only on its inputs, performs no I/O.
    """
    return op in capabilities


def gate_invocation(
    capabilities: frozenset[ProviderCapability],
    op: ProviderCapability,
) -> str | None:
    """Gate an attempted invocation of ``op`` against declared capabilities.

    Returns ``None`` when the operation is permitted, otherwise the error label
    ``'unsupported_operation'`` so the caller rejects the invocation. This
    function modifies no persisted Source_Item state -- it touches no state at
    all (Req 1.4).

    Args:
        capabilities: The provider's declared Provider_Capabilities.
        op: The operation the pipeline wants to invoke.

    Returns:
        None when ``op`` is supported, else ``'unsupported_operation'``.

    Pure and total: depends only on its inputs, performs no I/O.
    """
    if supports_capability(capabilities, op):
        return None
    return UNSUPPORTED_OPERATION


# ---------------------------------------------------------------------------
# Pure core: provider identity validation (Req 1.5, 1.6)
# ---------------------------------------------------------------------------

def identity_ok(item: SourceItem, expected_provider_id: str) -> bool:
    """Return True iff a returned candidate carries the expected provider identity.

    A returned Source_Item is valid only when its own Provider_Id and its
    Embed_Reference's Provider_Id both equal the producing provider's
    ``expected_provider_id`` AND its Embed_Reference carries a non-empty external
    item identifier. An item failing this check is rejected with a
    provider-identity mismatch and excluded from ingestion (Req 1.5, 1.6).

    Args:
        item: The candidate Source_Item returned by a provider.
        expected_provider_id: The Provider_Id of the provider that returned it.

    Returns:
        True iff ``item.provider_id == expected_provider_id`` AND
        ``item.embed_ref.provider_id == expected_provider_id`` AND
        ``item.embed_ref.external_id`` is non-empty.

    Pure and total: depends only on its inputs, performs no I/O.
    """
    return (
        item.provider_id == expected_provider_id
        and item.embed_ref.provider_id == expected_provider_id
        and bool(item.embed_ref.external_id)
    )


# ---------------------------------------------------------------------------
# Pure core: bounded search results (Req 1.1)
# ---------------------------------------------------------------------------

def bound_results(items: list[SourceItem], max_results: int) -> list[SourceItem]:
    """Return at most ``max_results`` items, preserving input order.

    The search contract never yields more than the configured maximum result
    count (Req 1.1). A non-positive ``max_results`` yields an empty list.

    Args:
        items: The candidate Source_Items a provider produced.
        max_results: The configured maximum number of results to return.

    Returns:
        An order-preserving prefix of ``items`` of length at most
        ``max(0, max_results)``.

    Pure and total: depends only on its inputs, performs no I/O.
    """
    if max_results <= 0:
        return []
    return list(items[:max_results])
