"""Integration test for provider failover and isolation (Task 10.3).

Exercises the ``acquire_sources`` orchestrator shell in
``app.services.source_acquirer`` end-to-end across MULTIPLE providers, verifying
the cross-provider resilience guarantees the pure cores cannot express on their
own:

  - **Failover (Req 6.4, 9.2).** When a selected provider's ``search`` raises an
    error -- or keeps returning nothing after its configured retries -- the
    acquirer records the failing provider's ``(provider_id, reason)`` in
    ``provider_failures`` and CONTINUES with the remaining eligible providers,
    admitting the items the successful provider returned.

  - **Isolation / no rollback (Req 9.4).** A failure in one provider never
    discards or rolls back the items already obtained from another provider; the
    successful provider's ``SourceItem``s come through intact.

  - **Non-blocking (Req 9.1, 9.4).** ``acquire_sources`` returns normally and
    NEVER raises, regardless of how a provider fails, so no learner-facing
    request can be made to fail by ingestion.

Everything is offline: the providers are in-memory fakes (one failing, one
healthy) and the per-provider charge seam ``source_acquirer.charge_before_call``
is monkeypatched to always allow, so the test isolates failover behaviour from
accounting and touches no real service.

Validates: Requirements 6.4, 9.2, 9.4
"""
from __future__ import annotations

import pytest

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
    REASON_NO_RESULTS,
    AcquisitionResult,
    acquire_sources,
)
from app.services.source_selection import ProviderAvailability

# A search costs this many Cost_Units; every provider declares it and every
# availability record is funded well above it, so budget never gates selection.
_SEARCH_COST = 10
_BUDGET = 1000
_MAX_RESULTS = 5


# --------------------------------------------------------------------------
# Fakes: ContentProvider implementations (offline, no external service)
# --------------------------------------------------------------------------


def _cost_policy() -> ProviderCostPolicy:
    """A Provider_Cost_Policy declaring an affordable search Cost_Unit."""
    return ProviderCostPolicy(
        cost_units={ProviderCapability.SEARCH: _SEARCH_COST},
        spend_budget=_BUDGET,
    )


def _source_item(provider_id: str, external_id: str, *, title: str) -> SourceItem:
    """A valid, identity-correct SourceItem from ``provider_id`` (Req 1.5)."""
    return SourceItem(
        provider_id=provider_id,
        embed_ref=EmbedReference(
            provider_id=provider_id,
            external_id=external_id,
            playback_params={"start": "0"},
        ),
        title=title,
        duration_seconds=120.0,
        transcript=[{"start": 0.0, "end": 5.0, "text": "hello"}],
        metadata={},
    )


class _HealthyProvider:
    """A provider whose ``search`` returns valid, identity-correct items."""

    def __init__(self, provider_id: str, items: list[SourceItem]):
        self._provider_id = provider_id
        self._items = items
        self.search_calls = 0

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset({ProviderCapability.SEARCH})

    @property
    def cost_policy(self) -> ProviderCostPolicy:
        return _cost_policy()

    def search(self, query: str, *, max_results: int) -> list[SourceItem]:
        self.search_calls += 1
        return list(self._items[:max_results])

    def fetch_metadata(self, external_id: str) -> dict:
        return {}

    def fetch_transcript(self, external_id: str):
        return None


class _RaisingProvider:
    """A provider whose ``search`` always raises -- the failover trigger (Req 6.4)."""

    def __init__(self, provider_id: str):
        self._provider_id = provider_id
        self.search_calls = 0

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset({ProviderCapability.SEARCH})

    @property
    def cost_policy(self) -> ProviderCostPolicy:
        return _cost_policy()

    def search(self, query: str, *, max_results: int) -> list[SourceItem]:
        self.search_calls += 1
        raise RuntimeError("boom: provider backend unavailable")

    def fetch_metadata(self, external_id: str) -> dict:
        return {}

    def fetch_transcript(self, external_id: str):
        return None


class _EmptyProvider:
    """A provider whose ``search`` always returns nothing (empty after retries)."""

    def __init__(self, provider_id: str):
        self._provider_id = provider_id
        self.search_calls = 0

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def capabilities(self) -> frozenset[ProviderCapability]:
        return frozenset({ProviderCapability.SEARCH})

    @property
    def cost_policy(self) -> ProviderCostPolicy:
        return _cost_policy()

    def search(self, query: str, *, max_results: int) -> list[SourceItem]:
        self.search_calls += 1
        return []

    def fetch_metadata(self, external_id: str) -> dict:
        return {}

    def fetch_transcript(self, external_id: str):
        return None


# --------------------------------------------------------------------------
# Test setup: registry, availability, and an always-allow charge seam
# --------------------------------------------------------------------------


def _record(provider_id: str) -> ProviderRecord:
    """An enabled, search-capable registry record for ``provider_id``."""
    return ProviderRecord(
        provider_id=provider_id,
        enabled=True,
        capabilities=frozenset({ProviderCapability.SEARCH}),
        cost_policy=_cost_policy(),
    )


def _registry(*provider_ids: str) -> Registry:
    return Registry(records=tuple(_record(pid) for pid in provider_ids))


def _availability(*provider_ids: str) -> dict[str, ProviderAvailability]:
    """Every provider available and funded well above the search Cost_Unit."""
    return {
        pid: ProviderAvailability(
            provider_id=pid,
            remaining_budget=_BUDGET,
            available=True,
        )
        for pid in provider_ids
    }


@pytest.fixture
def allow_all_charges(monkeypatch):
    """Monkeypatch the charge seam to always allow, isolating failover from spend.

    ``source_acquirer`` does ``from app.services.provider_cost import
    charge_before_call``, binding the name into its own module namespace, so the
    patch target is ``source_acquirer.charge_before_call``.
    """
    calls: list[tuple] = []

    def _always_allow(provider_id, op, *, cache_hit, now_utc=None):
        calls.append((provider_id, op, cache_hit))
        return True

    monkeypatch.setattr(source_acquirer, "charge_before_call", _always_allow)
    return calls


# --------------------------------------------------------------------------
# Req 6.4, 9.2, 9.4: failover past a raising provider
# --------------------------------------------------------------------------


class TestFailoverPastRaisingProvider:
    def test_continues_past_raising_provider_and_admits_successful_items(
        self, allow_all_charges
    ):
        # "aaa_fail" sorts before "bbb_ok", so select_sources queries the failing
        # provider FIRST -- the acquirer must continue past it to the healthy one.
        good_items = [
            _source_item("bbb_ok", "v1", title="One"),
            _source_item("bbb_ok", "v2", title="Two"),
        ]
        failing = _RaisingProvider("aaa_fail")
        healthy = _HealthyProvider("bbb_ok", good_items)

        result = acquire_sources(
            _registry("aaa_fail", "bbb_ok"),
            query="binary search",
            availability=_availability("aaa_fail", "bbb_ok"),
            providers={"aaa_fail": failing, "bbb_ok": healthy},
            max_results=_MAX_RESULTS,
        )

        assert isinstance(result, AcquisitionResult)
        # Both providers were actually queried: failover did not stop at the
        # first failure (Req 6.4).
        assert failing.search_calls >= 1
        assert healthy.search_calls == 1
        # The healthy provider's items came through intact -- no rollback (Req 9.4).
        assert result.items == good_items
        # The failing provider is recorded with its (provider_id, reason) (Req 6.4).
        failed_ids = [pid for pid, _ in result.provider_failures]
        assert failed_ids == ["aaa_fail"]
        reason = result.provider_failures[0][1]
        assert "search_error" in reason
        # No deferral: at least one provider succeeded.
        assert result.defer_reason is None

    def test_raising_provider_retried_then_recorded(self, allow_all_charges):
        # A provider raising on every attempt is retried up to its configured
        # maximum (default 3) before being recorded as failed (Req 9.2).
        failing = _RaisingProvider("aaa_fail")
        healthy = _HealthyProvider(
            "bbb_ok", [_source_item("bbb_ok", "v1", title="One")]
        )

        result = acquire_sources(
            _registry("aaa_fail", "bbb_ok"),
            query="q",
            availability=_availability("aaa_fail", "bbb_ok"),
            providers={"aaa_fail": failing, "bbb_ok": healthy},
            max_results=_MAX_RESULTS,
            retries=3,
        )

        assert failing.search_calls == 3  # retried to the configured maximum
        assert [pid for pid, _ in result.provider_failures] == ["aaa_fail"]
        assert len(result.items) == 1


# --------------------------------------------------------------------------
# Req 6.4, 9.2: a provider empty after retries is a failure, others still admit
# --------------------------------------------------------------------------


class TestFailoverPastEmptyProvider:
    def test_empty_after_retries_recorded_and_other_items_admitted(
        self, allow_all_charges
    ):
        empty = _EmptyProvider("aaa_empty")
        good_items = [_source_item("bbb_ok", "v9", title="Nine")]
        healthy = _HealthyProvider("bbb_ok", good_items)

        result = acquire_sources(
            _registry("aaa_empty", "bbb_ok"),
            query="q",
            availability=_availability("aaa_empty", "bbb_ok"),
            providers={"aaa_empty": empty, "bbb_ok": healthy},
            max_results=_MAX_RESULTS,
        )

        # Empty-after-retries is a no-results failure recorded against the
        # provider (Req 6.4) while the healthy provider's items are still admitted.
        assert result.provider_failures == [("aaa_empty", REASON_NO_RESULTS)]
        assert result.items == good_items
        assert result.defer_reason is None


# --------------------------------------------------------------------------
# Req 9.4: isolation -- one provider's failure does not block the other's items
# --------------------------------------------------------------------------


class TestProviderIsolation:
    def test_failure_in_one_provider_does_not_block_the_other(
        self, allow_all_charges
    ):
        # Order the HEALTHY provider first and the failing one second to prove the
        # already-obtained items survive a later provider's failure (no rollback).
        good_items = [
            _source_item("aaa_ok", "a1", title="A1"),
            _source_item("aaa_ok", "a2", title="A2"),
        ]
        healthy = _HealthyProvider("aaa_ok", good_items)
        failing = _RaisingProvider("zzz_fail")

        result = acquire_sources(
            _registry("aaa_ok", "zzz_fail"),
            query="q",
            availability=_availability("aaa_ok", "zzz_fail"),
            providers={"aaa_ok": healthy, "zzz_fail": failing},
            max_results=_MAX_RESULTS,
        )

        # The healthy provider's items are intact despite the later failure (Req 9.4).
        assert result.items == good_items
        assert [pid for pid, _ in result.provider_failures] == ["zzz_fail"]

    def test_acquire_sources_returns_normally_and_never_raises(
        self, allow_all_charges
    ):
        # Even when EVERY selected provider fails, the call returns an
        # AcquisitionResult rather than raising -- ingestion is non-blocking and
        # never fails a learner-facing request (Req 9.1, 9.4).
        failing_a = _RaisingProvider("aaa_fail")
        failing_b = _EmptyProvider("bbb_empty")

        result = acquire_sources(
            _registry("aaa_fail", "bbb_empty"),
            query="q",
            availability=_availability("aaa_fail", "bbb_empty"),
            providers={"aaa_fail": failing_a, "bbb_empty": failing_b},
            max_results=_MAX_RESULTS,
        )

        assert isinstance(result, AcquisitionResult)
        # No items admitted, but both failures are recorded and nothing raised.
        assert result.items == []
        failed_ids = sorted(pid for pid, _ in result.provider_failures)
        assert failed_ids == ["aaa_fail", "bbb_empty"]
        # Not a selection deferral: providers WERE selected and queried, they failed.
        assert result.defer_reason is None
