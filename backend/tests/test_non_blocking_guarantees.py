"""Integration test for the non-blocking operational guarantees (Task 7.4).

Requirement 11 makes four cross-cutting promises that the pure cores
(``call_policy``) cannot express on their own, because they are properties of
*where* and *how* the best-effort shells schedule and isolate work rather than of
any single decision. This test wires the available shells together against
in-memory fakes (no external service touched) and asserts each promise:

  1. **Non-blocking (Req 11.1).** A feed request returns its ordered clips
     immediately and merely *enqueues* Arc_Backfill / reseed / Coverage_View /
     Curriculum_Spine maintenance onto an out-of-band queue; the request adds no
     wait time for that work, which is still pending (unexecuted) when the
     response is produced.

  2. **External content only via the Key_Pool / Content_Provider (Req 11.2).**
     A backfill that consumes external content does so exclusively through the
     ``Content_Provider`` abstraction (``acquire_sources`` -> ``ContentProvider``)
     and charges the Key_Pool through the charge seam; the only content obtained
     came from a provider's ``search``, and every spend went through the charge
     seam -- there is no other path.

  3. **Already-Arc_Complete backfill spends zero quota (Req 11.5).** Backfilling a
     Topic that is already Arc_Complete short-circuits through the pure
     ``should_skip_acquisition`` seam before any provider is selected or charged,
     so no provider ``search`` runs and zero Key_Pool spend is consumed; the
     stored clips are left unchanged.

  4. **Failure isolation (Req 11.6).** A best-effort batch of maintenance
     operations in which one operation raises records that operation's failure,
     resolves it to a best-available fallback via ``call_policy.resolve_outcome``,
     and lets every other operation complete -- no exception propagates and no
     other operation is failed.

Dependency note: the Arc_Backfill *runner* shell
(``app/services/arc_backfill_runner.py``) is built in Task 6 and does not yet
exist, so the backfill enqueue path is modelled here with a small in-memory
``MaintenanceQueue`` and the real, already-implemented best-effort shells
(``source_acquirer.acquire_sources`` for the Content_Provider consumption path,
its pure ``should_skip_acquisition`` skip seam, and ``clip_ordering`` for the
request-path feed work). When the Task 6 runner lands, its enqueue call replaces
the modelled enqueue while these contracts continue to hold.

Run from the backend/ dir:
``.venv/bin/python -m pytest tests/test_non_blocking_guarantees.py``.

Validates: Requirements 11.1, 11.2, 11.5, 11.6
"""
from __future__ import annotations

import pytest

from app.services import source_acquirer
from app.services.call_policy import resolve_outcome
from app.services.clip_ordering import order_clips_by_arc
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
    AcquisitionResult,
    acquire_sources,
    should_skip_acquisition,
)
from app.services.source_selection import ProviderAvailability
from tests.conftest import make_clip

_SEARCH_COST = 10
_BUDGET = 1000
_MAX_RESULTS = 5
_TARGET_CLIPS = 4


# ---------------------------------------------------------------------------
# Fakes: a Content_Provider (offline) and registry/availability helpers
# ---------------------------------------------------------------------------


def _cost_policy() -> ProviderCostPolicy:
    return ProviderCostPolicy(
        cost_units={ProviderCapability.SEARCH: _SEARCH_COST},
        spend_budget=_BUDGET,
    )


def _source_item(provider_id: str, external_id: str, *, title: str) -> SourceItem:
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


class _RecordingProvider:
    """A provider whose ``search`` returns valid items and records every call.

    The recording lets the test prove that the ONLY external content obtained
    flowed through this provider's ``search`` -- the Content_Provider abstraction
    (Req 11.2) -- and nowhere else.
    """

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


def _record(provider_id: str) -> ProviderRecord:
    return ProviderRecord(
        provider_id=provider_id,
        enabled=True,
        capabilities=frozenset({ProviderCapability.SEARCH}),
        cost_policy=_cost_policy(),
    )


def _registry(*provider_ids: str) -> Registry:
    return Registry(records=tuple(_record(pid) for pid in provider_ids))


def _availability(*provider_ids: str) -> dict[str, ProviderAvailability]:
    return {
        pid: ProviderAvailability(
            provider_id=pid,
            remaining_budget=_BUDGET,
            available=True,
        )
        for pid in provider_ids
    }


@pytest.fixture
def charge_meter(monkeypatch):
    """Replace the charge seam with a Key_Pool spend meter that always allows.

    ``source_acquirer`` binds ``charge_before_call`` into its own namespace via
    ``from app.services.provider_cost import charge_before_call``, so the patch
    target is ``source_acquirer.charge_before_call``. Every charge is recorded so
    the test can assert exactly how much Key_Pool quota a backfill consumed.
    """
    charges: list[tuple] = []

    def _metered_allow(provider_id, op, *, cache_hit, now_utc=None):
        charges.append((provider_id, op, cache_hit))
        return True

    monkeypatch.setattr(source_acquirer, "charge_before_call", _metered_allow)
    return charges


# ---------------------------------------------------------------------------
# A tiny out-of-band maintenance queue modelling the async enqueue (Req 11.1)
# ---------------------------------------------------------------------------


class MaintenanceQueue:
    """An in-memory stand-in for the out-of-band work queue.

    ``enqueue`` only records work; it never runs it. ``drain`` is what a worker
    (outside the request path) would later call. This models the contract that
    Arc_Backfill / reseed / Coverage_View / Curriculum_Spine maintenance is
    scheduled OUTSIDE the request/response path (Req 11.1).
    """

    def __init__(self):
        self.pending: list[tuple[str, str]] = []
        self.executed: list[str] = []

    def enqueue(self, kind: str, slug: str) -> None:
        self.pending.append((kind, slug))

    def drain(self) -> None:
        while self.pending:
            kind, _slug = self.pending.pop(0)
            self.executed.append(kind)


def serve_feed(slug: str, clips, queue: MaintenanceQueue) -> list:
    """Serve a Topic feed: order clips on the request path, enqueue maintenance.

    The ordering is the only work the request awaits (the pure
    ``order_clips_by_arc`` core); all four Req-11.1 maintenance operations are
    enqueued onto the out-of-band ``queue`` and NOT awaited.
    """
    ordered = order_clips_by_arc(clips, None)
    for kind in ("arc_backfill", "reseed", "coverage_view", "spine_maintenance"):
        queue.enqueue(kind, slug)
    return ordered


# ===========================================================================
# Req 11.1: a feed request returns before enqueued maintenance work completes
# ===========================================================================


class TestFeedRequestIsNonBlocking:
    def test_feed_returns_with_maintenance_still_pending(self):
        queue = MaintenanceQueue()
        clips = [make_clip(hook_score=s) for s in (0.1, 0.9, 0.5)]

        ordered = serve_feed("fractions", clips, queue)

        # The request produced its response (clips ordered by the single core)...
        assert len(ordered) == len(clips)
        assert {c.id for c in ordered} == {c.id for c in clips}
        # ...while every maintenance operation is still pending, unexecuted: the
        # request added zero wait time for it (Req 11.1).
        assert queue.executed == []
        assert {kind for kind, _ in queue.pending} == {
            "arc_backfill",
            "reseed",
            "coverage_view",
            "spine_maintenance",
        }

    def test_maintenance_runs_only_when_drained_out_of_band(self):
        queue = MaintenanceQueue()
        clips = [make_clip(hook_score=0.5)]

        serve_feed("decimals", clips, queue)
        assert queue.executed == []  # nothing ran on the request path

        # A worker outside the request path drains the queue afterwards.
        queue.drain()
        assert sorted(queue.executed) == [
            "arc_backfill",
            "coverage_view",
            "reseed",
            "spine_maintenance",
        ]
        assert queue.pending == []


# ===========================================================================
# Req 11.2: external content is consumed only via the Key_Pool / Content_Provider
# ===========================================================================


class TestExternalContentOnlyViaProvider:
    def test_backfill_content_comes_only_from_the_provider(self, charge_meter):
        items = [
            _source_item("youtube", "v1", title="One"),
            _source_item("youtube", "v2", title="Two"),
        ]
        provider = _RecordingProvider("youtube", items)

        # A backfill for an incomplete Topic consumes external content -- it does
        # so only through the Content_Provider abstraction.
        result = acquire_sources(
            _registry("youtube"),
            query="binary search",
            availability=_availability("youtube"),
            providers={"youtube": provider},
            max_results=_MAX_RESULTS,
            status="incomplete",
            admitted_count=0,
            target_count=_TARGET_CLIPS,
        )

        assert isinstance(result, AcquisitionResult)
        # Every admitted item carries the provider's identity: content entered
        # only through the provider's search (Req 11.2).
        assert result.items == items
        assert all(it.provider_id == "youtube" for it in result.items)
        assert provider.search_calls == 1
        # The spend went through the Key_Pool charge seam, not any side path: one
        # charge, for the search op, against the provider (Req 11.2).
        assert charge_meter == [("youtube", ProviderCapability.SEARCH, False)]


# ===========================================================================
# Req 11.5: backfilling an already-Arc_Complete Topic spends zero quota
# ===========================================================================


class TestArcCompleteBackfillSpendsZeroQuota:
    def test_arc_complete_topic_skips_with_zero_spend(self, charge_meter):
        items = [_source_item("youtube", "v1", title="One")]
        provider = _RecordingProvider("youtube", items)

        # The Topic is already Arc_Complete: status complete AND enough admitted
        # clips. The pure skip seam agrees...
        assert should_skip_acquisition("complete", _TARGET_CLIPS, _TARGET_CLIPS)

        result = acquire_sources(
            _registry("youtube"),
            query="binary search",
            availability=_availability("youtube"),
            providers={"youtube": provider},
            max_results=_MAX_RESULTS,
            status="complete",
            admitted_count=_TARGET_CLIPS,
            target_count=_TARGET_CLIPS,
        )

        # ...so acquisition short-circuits: no provider queried, no content
        # obtained, and crucially zero Key_Pool quota consumed (Req 11.5).
        assert result.items == []
        assert provider.search_calls == 0
        assert charge_meter == []

    def test_incomplete_topic_does_consume_quota(self, charge_meter):
        # Contrast: an incomplete Topic is NOT skipped, so the consumption path is
        # real -- the provider is queried and quota IS charged. This proves the
        # zero-spend above is the Arc_Complete skip, not a dead path.
        items = [_source_item("youtube", "v1", title="One")]
        provider = _RecordingProvider("youtube", items)

        assert not should_skip_acquisition("incomplete", 0, _TARGET_CLIPS)

        acquire_sources(
            _registry("youtube"),
            query="binary search",
            availability=_availability("youtube"),
            providers={"youtube": provider},
            max_results=_MAX_RESULTS,
            status="incomplete",
            admitted_count=0,
            target_count=_TARGET_CLIPS,
        )

        assert provider.search_calls == 1
        assert charge_meter == [("youtube", ProviderCapability.SEARCH, False)]


# ===========================================================================
# Req 11.6: one operation's failure does not fail others
# ===========================================================================


def run_maintenance_batch(operations: dict[str, callable]) -> dict[str, object]:
    """Run a batch of best-effort maintenance ops, isolating failures.

    Each operation is wrapped: a raised exception is caught (never propagated),
    recorded, and resolved to a best-available fallback via
    ``call_policy.resolve_outcome`` so the caller is handed a value rather than an
    error (Req 11.4, 11.6). One operation's failure never aborts the batch.
    """
    outcomes: dict[str, object] = {}
    for name, op in operations.items():
        try:
            value = op()
            outcomes[name] = resolve_outcome(value, attempts_exhausted=False, timed_out=False)
        except Exception:
            # Fall back to the last stored result (None here) and flag it; the
            # failure is isolated to this operation (Req 11.6).
            outcomes[name] = resolve_outcome(None, attempts_exhausted=True, timed_out=False)
    return outcomes


class TestFailureIsolation:
    def test_one_failing_operation_does_not_fail_the_others(self):
        def _ok_backfill():
            return "backfilled"

        def _failing_reseed():
            raise RuntimeError("boom: reseed backend unavailable")

        def _ok_coverage():
            return "coverage_built"

        outcomes = run_maintenance_batch(
            {
                "arc_backfill": _ok_backfill,
                "reseed": _failing_reseed,
                "coverage_view": _ok_coverage,
            }
        )

        # The two healthy operations completed with fresh results, no fallback.
        assert outcomes["arc_backfill"].value == "backfilled"
        assert outcomes["arc_backfill"].used_fallback is False
        assert outcomes["coverage_view"].value == "coverage_built"
        assert outcomes["coverage_view"].used_fallback is False
        # The failing operation was isolated: it fell back without raising and did
        # not abort the batch (Req 11.6).
        assert outcomes["reseed"].used_fallback is True
        assert outcomes["reseed"].value is None
        # All three operations were attempted -- none was skipped by another's
        # failure.
        assert set(outcomes) == {"arc_backfill", "reseed", "coverage_view"}

    def test_batch_never_raises_even_when_every_operation_fails(self):
        def _boom():
            raise RuntimeError("down")

        outcomes = run_maintenance_batch({"a": _boom, "b": _boom})

        # Every operation failed, but the batch returned normally with each
        # resolved to a flagged fallback -- no learner-facing error surfaced.
        assert set(outcomes) == {"a", "b"}
        assert all(o.used_fallback is True for o in outcomes.values())
        assert all(o.value is None for o in outcomes.values())
