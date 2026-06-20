"""Integration tests for the Source_Acquirer <-> Ingestion_Pipeline boundary (Task 10.4).

The Source_Acquirer (``app.services.source_acquirer.acquire_sources``) is the thin
best-effort I/O shell that turns a Provider_Registry snapshot plus live
availability into a bounded, de-duplicated set of identity-valid ``SourceItem``s
ready to feed the UNCHANGED shared ``ingest_topic`` decode stage. These tests
exercise the *boundary* between that shell and the shared pipeline, asserting the
acquirer's ACTUAL behavior as implemented in ``source_acquirer.py`` (verified by
reading it):

  - The acquirer produces identity-valid, de-duplicated ``SourceItem``s for valid
    providers and records provider / normalization failures, but performs NO
    persistence and admits NO Clip itself -- admission is solely the shared
    pipeline's job (Req 5.6, 5.7 boundary aspect: the acquirer never admits a
    Clip and keeps no partial Clip output).

  - The acquirer gates provider invocation by the ``search`` Provider_Capability
    ONLY; it does NOT fetch transcripts and does NOT enforce the
    ``fetch_transcript`` requirement. A provider lacking the ``fetch_transcript``
    capability (and whose items therefore carry no Transcript) is still searched
    and still yields ``SourceItem``s from the acquirer -- the missing-Transcript
    skip of Req 5.5 is owned downstream by ``ingest_topic``, not the acquirer.
    These tests pin that division of responsibility at the boundary.

Everything is offline: ``charge_before_call`` is monkeypatched to return ``True``
so no cost-accounting I/O runs, and the Supabase client is replaced by a spy that
proves the acquirer touches no database.

Validates: Requirements 5.5, 5.6, 5.7 (acquirer <-> pipeline boundary aspects)
"""
from __future__ import annotations

import pytest

import app.db.supabase as supabase_mod
from app.services import source_acquirer
from app.services.content_provider import (
    EmbedReference,
    ProviderCapability,
    SourceItem,
)
from app.services.provider_registry import (
    ProviderCostPolicy,
    ProviderRecord,
    Registry,
)
from app.services.source_acquirer import (
    REASON_IDENTITY_MISMATCH,
    REASON_NO_PROVIDER_INSTANCE,
    acquire_sources,
)
from app.services.source_selection import ProviderAvailability

# Every capability EXCEPT search-only providers declare; a "full" provider also
# declares fetch_transcript so we can contrast it with a transcript-less one.
_SEARCH_ONLY = frozenset({ProviderCapability.SEARCH})
_FULL = frozenset(
    {
        ProviderCapability.SEARCH,
        ProviderCapability.FETCH_METADATA,
        ProviderCapability.FETCH_TRANSCRIPT,
    }
)

_BUDGET = 1000
_SEARCH_COST = 10
_MAX_RESULTS = 10


# ---------------------------------------------------------------------------
# Offline doubles
# ---------------------------------------------------------------------------


class FakeProvider:
    """A minimal ``ContentProvider`` double for the acquirer boundary.

    ``search`` returns the configured items (bounded by ``max_results``) or raises
    when ``raises`` is set. ``fetch_transcript`` exists only so the object honors
    the Protocol shape; the acquirer never calls it.
    """

    def __init__(self, provider_id, capabilities, *, items=None, raises=False):
        self._provider_id = provider_id
        self._capabilities = capabilities
        self._items = list(items or [])
        self._raises = raises
        self.search_calls = 0

    @property
    def provider_id(self):
        return self._provider_id

    @property
    def capabilities(self):
        return self._capabilities

    @property
    def cost_policy(self):
        return None

    def search(self, query, *, max_results):
        self.search_calls += 1
        if self._raises:
            raise RuntimeError("provider search boom")
        return list(self._items[:max_results])

    def fetch_metadata(self, external_id):
        return {}

    def fetch_transcript(self, external_id):
        return None


class _ClientSpy:
    """Counts how often the Supabase client is requested by the code under test."""

    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        raise AssertionError("acquire_sources must not touch the database")


def _item(provider_id, external_id, *, embed_provider_id=None, transcript=None):
    """Build a SourceItem; ``embed_provider_id`` overrides the embed's id for mismatch tests."""
    embed = EmbedReference(
        provider_id=embed_provider_id if embed_provider_id is not None else provider_id,
        external_id=external_id,
        playback_params={},
    )
    return SourceItem(
        provider_id=provider_id,
        embed_ref=embed,
        title=f"{provider_id}:{external_id}",
        duration_seconds=None,
        transcript=transcript,
        metadata={},
    )


def _record(provider_id, *, enabled=True, capabilities=_FULL, search_cost=_SEARCH_COST):
    return ProviderRecord(
        provider_id=provider_id,
        enabled=enabled,
        capabilities=capabilities,
        cost_policy=ProviderCostPolicy(
            cost_units={ProviderCapability.SEARCH: search_cost},
            spend_budget=_BUDGET,
        ),
    )


def _registry(*records):
    return Registry(records=tuple(records))


def _avail(provider_id, *, budget=_BUDGET, available=True):
    return ProviderAvailability(
        provider_id=provider_id, remaining_budget=budget, available=available
    )


@pytest.fixture(autouse=True)
def _free_charges(monkeypatch):
    """Monkeypatch the acquirer's charge seam to always allow (offline, Req per task)."""
    monkeypatch.setattr(
        source_acquirer, "charge_before_call", lambda *a, **k: True
    )


@pytest.fixture
def _no_db(monkeypatch):
    """Replace the Supabase client with a spy proving the acquirer does no DB I/O."""
    spy = _ClientSpy()
    monkeypatch.setattr(supabase_mod, "get_client", spy)
    return spy


# ---------------------------------------------------------------------------
# The acquirer produces SourceItems and never persists / admits (Req 5.6, 5.7)
# ---------------------------------------------------------------------------


class TestProducesSourceItemsOnly:
    def test_valid_providers_yield_dedup_sourceitems_in_selection_order(self):
        a1 = _item("alpha", "a1")
        b1 = _item("beta", "b1")
        registry = _registry(_record("alpha"), _record("beta"))
        providers = {
            "alpha": FakeProvider("alpha", _FULL, items=[a1]),
            "beta": FakeProvider("beta", _FULL, items=[b1]),
        }
        availability = {"alpha": _avail("alpha"), "beta": _avail("beta")}

        result = acquire_sources(
            registry,
            "query",
            availability,
            providers,
            max_results=_MAX_RESULTS,
        )

        # Identity-valid items from both providers, in ascending Provider_Id order.
        assert result.items == [a1, b1]
        assert all(isinstance(it, SourceItem) for it in result.items)
        assert result.provider_failures == []
        assert result.normalization_failures == []
        assert result.defer_reason is None

    def test_acquirer_touches_no_database(self, _no_db):
        a1 = _item("alpha", "a1")
        registry = _registry(_record("alpha"))
        providers = {"alpha": FakeProvider("alpha", _FULL, items=[a1])}

        result = acquire_sources(
            registry,
            "query",
            {"alpha": _avail("alpha")},
            providers,
            max_results=_MAX_RESULTS,
        )

        # The acquirer produced a SourceItem but never requested a DB client, so it
        # persisted nothing and admitted no Clip itself -- admission is solely the
        # shared pipeline's job (Req 5.6, 5.7 boundary).
        assert result.items == [a1]
        assert _no_db.calls == 0

    def test_result_model_has_no_admitted_clip_channel(self):
        # The acquirer's only positive output is a list of SourceItems; there is no
        # admit / Clip field on AcquisitionResult, so it cannot itself admit a Clip.
        a1 = _item("alpha", "a1")
        registry = _registry(_record("alpha"))
        result = acquire_sources(
            registry,
            "query",
            {"alpha": _avail("alpha")},
            {"alpha": FakeProvider("alpha", _FULL, items=[a1])},
            max_results=_MAX_RESULTS,
        )

        fields = set(result.__dataclass_fields__)
        assert fields == {
            "items",
            "defer_reason",
            "provider_failures",
            "normalization_failures",
        }
        # Every positive output is a transcript-ready SourceItem, never a Clip.
        assert all(isinstance(it, SourceItem) for it in result.items)

    def test_same_identity_items_are_deduplicated_to_one(self):
        # One provider returning the same (provider_id, external_id) twice collapses
        # to a single admitted SourceItem -- the acquirer hands the pipeline a
        # de-duplicated set (Req 8.2 boundary), never a duplicate.
        dup_a = _item("alpha", "same")
        dup_b = _item("alpha", "same")
        registry = _registry(_record("alpha"))
        providers = {"alpha": FakeProvider("alpha", _FULL, items=[dup_a, dup_b])}

        result = acquire_sources(
            registry,
            "query",
            {"alpha": _avail("alpha")},
            providers,
            max_results=_MAX_RESULTS,
        )

        assert len(result.items) == 1
        assert result.items[0].embed_ref.external_id == "same"


# ---------------------------------------------------------------------------
# Req 5.5 boundary: the acquirer does NOT enforce the transcript requirement
# ---------------------------------------------------------------------------


class TestTranscriptRequirementOwnedDownstream:
    def test_search_only_provider_without_fetch_transcript_still_yields_items(self):
        # A provider declaring ONLY `search` (no `fetch_transcript`) whose items
        # carry no Transcript is still searched and still produces SourceItems from
        # the acquirer: the acquirer gates on `search` capability alone and does not
        # itself perform the Req 5.5 missing-Transcript skip. That skip is owned by
        # the shared ingest_topic decode stage, not by the acquirer.
        no_transcript = _item("alpha", "a1", transcript=None)
        registry = _registry(_record("alpha", capabilities=_SEARCH_ONLY))
        providers = {"alpha": FakeProvider("alpha", _SEARCH_ONLY, items=[no_transcript])}

        result = acquire_sources(
            registry,
            "query",
            {"alpha": _avail("alpha")},
            providers,
            max_results=_MAX_RESULTS,
        )

        # The transcript-less item is produced (NOT skipped) at the acquirer level,
        # and nothing is recorded as a failure here -- the skip happens downstream.
        assert result.items == [no_transcript]
        assert result.items[0].transcript is None
        assert result.provider_failures == []
        assert result.normalization_failures == []

    def test_search_capable_provider_is_invoked_regardless_of_transcript_capability(self):
        # Two providers both declare `search`; one additionally declares
        # `fetch_transcript`, the other does not. Both are searched and both
        # contribute SourceItems -- transcript capability does not change selection
        # or acquisition at this boundary.
        full_item = _item("alpha", "a1", transcript=[{"text": "hi", "start": 0.0}])
        thin_item = _item("beta", "b1", transcript=None)
        registry = _registry(
            _record("alpha", capabilities=_FULL),
            _record("beta", capabilities=_SEARCH_ONLY),
        )
        providers = {
            "alpha": FakeProvider("alpha", _FULL, items=[full_item]),
            "beta": FakeProvider("beta", _SEARCH_ONLY, items=[thin_item]),
        }
        availability = {"alpha": _avail("alpha"), "beta": _avail("beta")}

        result = acquire_sources(
            registry, "query", availability, providers, max_results=_MAX_RESULTS
        )

        assert result.items == [full_item, thin_item]
        assert providers["alpha"].search_calls == 1
        assert providers["beta"].search_calls == 1
        assert result.provider_failures == []


# ---------------------------------------------------------------------------
# Failures are recorded; one provider's failure never admits/loses another's
# ---------------------------------------------------------------------------


class TestFailuresRecordedNothingAdmitted:
    def test_identity_mismatch_recorded_and_item_discarded(self):
        # An item whose embed Provider_Id disagrees with the producing provider is
        # discarded and recorded as a normalization failure -- no SourceItem leaks
        # to the pipeline from it (Req 1.6 / boundary of 5.5-5.7: nothing admitted).
        bad = _item("alpha", "a1", embed_provider_id="someone_else")
        good = _item("alpha", "a2")
        registry = _registry(_record("alpha"))
        providers = {"alpha": FakeProvider("alpha", _FULL, items=[bad, good])}

        result = acquire_sources(
            registry,
            "query",
            {"alpha": _avail("alpha")},
            providers,
            max_results=_MAX_RESULTS,
        )

        assert result.items == [good]
        assert ("alpha", REASON_IDENTITY_MISMATCH) in result.normalization_failures

    def test_failing_provider_recorded_others_still_acquired(self):
        # One provider raises on every retry; the other succeeds. The acquirer
        # records the failure against its Provider_Id and still returns the healthy
        # provider's SourceItems -- a failure in one provider never blocks the
        # others and admits nothing partial (Req 6.4, 9.2, 9.4 boundary).
        b1 = _item("beta", "b1")
        registry = _registry(_record("alpha"), _record("beta"))
        providers = {
            "alpha": FakeProvider("alpha", _FULL, raises=True),
            "beta": FakeProvider("beta", _FULL, items=[b1]),
        }
        availability = {"alpha": _avail("alpha"), "beta": _avail("beta")}

        result = acquire_sources(
            registry, "query", availability, providers, max_results=_MAX_RESULTS
        )

        assert result.items == [b1]
        failed_ids = [pid for pid, _reason in result.provider_failures]
        assert "alpha" in failed_ids
        # alpha retried up to its default maximum before being recorded as failed.
        assert providers["alpha"].search_calls == source_acquirer.DEFAULT_RETRIES

    def test_selected_provider_without_instance_is_recorded_not_admitted(self):
        # A provider selected from the registry but absent from the live providers
        # map is recorded as a failure and contributes no SourceItem.
        registry = _registry(_record("alpha"), _record("beta"))
        b1 = _item("beta", "b1")
        providers = {"beta": FakeProvider("beta", _FULL, items=[b1])}  # no "alpha"
        availability = {"alpha": _avail("alpha"), "beta": _avail("beta")}

        result = acquire_sources(
            registry, "query", availability, providers, max_results=_MAX_RESULTS
        )

        assert result.items == [b1]
        assert ("alpha", REASON_NO_PROVIDER_INSTANCE) in result.provider_failures
