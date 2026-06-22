"""Property-based test for idempotent-skip budget consumption.

# Feature: alternative-content-streams, Property 17: Idempotent skip consumes zero budget

*For any* Topic whose persisted state marks it as coherently ingested -- its
ingestion status is ``complete`` AND its admitted-clip count is at or above the
configured target -- the acquisition path is skipped so that no provider's
``charge_decision`` is invoked and zero ``Spend_Budget`` is consumed from every
provider, leaving stored clips unchanged (Req 9.3).

This test covers the decision seam from two angles:

  1. ``should_skip_acquisition`` is True *exactly* when ``status == 'complete'``
     AND ``admitted_count >= target_count``, generating ``complete`` alongside
     other strings and ``None`` and arbitrary non-negative counts.
  2. When those skip conditions hold, ``acquire_sources`` short-circuits: a spy
     ``ContentProvider`` whose ``search`` would record an invocation is NEVER
     called, and the ``charge_before_call`` seam (monkeypatched to count every
     invocation) is NEVER reached -- so no charge happens -- and the returned
     result carries no items.

Imports only the module under test (``source_acquirer``) plus the ``monkeypatch``
fixture, so it runs offline with no external service (evidence for Req 9.5).

Validates: Requirements 9.3
"""
from hypothesis import HealthCheck, given, settings, strategies as st

from app.services.source_acquirer import (
    STATUS_COMPLETE,
    acquire_sources,
    should_skip_acquisition,
)


class _SpyProvider:
    """A minimal ContentProvider-shaped spy that records whether it was searched.

    A charge would be incurred if ``search`` ran, so an untouched ``search_called``
    flag is direct evidence the acquisition path was skipped before any provider
    work (and therefore before any spend).
    """

    def __init__(self) -> None:
        self.search_called = False

    @property
    def provider_id(self) -> str:
        return "spy"

    @property
    def capabilities(self):
        return frozenset()

    @property
    def cost_policy(self):
        return None

    def search(self, query: str, *, max_results: int):
        self.search_called = True
        return []

    def fetch_metadata(self, external_id: str) -> dict:
        return {}

    def fetch_transcript(self, external_id: str):
        return None


# A status that is generated to include 'complete', other arbitrary strings, and
# None, so the iff boundary is exercised on both sides.
_status = st.one_of(
    st.just(STATUS_COMPLETE),
    st.text(max_size=20),
    st.none(),
)

_count = st.integers(min_value=0, max_value=10_000)


class TestIdempotentSkip:
    @settings(max_examples=100)
    @given(status=_status, admitted_count=_count, target_count=_count)
    def test_skip_iff_complete_and_target_met(self, status, admitted_count, target_count):
        # should_skip_acquisition is True exactly when the Topic is coherently
        # ingested: status complete AND admitted count at/above the target.
        expected = status == STATUS_COMPLETE and admitted_count >= target_count
        assert should_skip_acquisition(status, admitted_count, target_count) is expected

    @settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    @given(target_count=_count, extra=_count)
    def test_skip_consumes_zero_budget(self, monkeypatch, target_count, extra):
        # Construct skip conditions: status complete and admitted >= target.
        admitted_count = target_count + extra

        # Spy the charge seam: any invocation means budget would be consumed.
        charges: list[tuple] = []

        def _spy_charge(*args, **kwargs):
            charges.append((args, kwargs))
            return True

        monkeypatch.setattr(
            "app.services.source_acquirer.charge_before_call", _spy_charge
        )

        spy = _SpyProvider()

        result = acquire_sources(
            registry=None,  # never read on the skip path
            query="anything",
            availability={},
            providers={"spy": spy},
            max_results=5,
            status=STATUS_COMPLETE,
            admitted_count=admitted_count,
            target_count=target_count,
        )

        # The skip conditions hold...
        assert should_skip_acquisition(
            STATUS_COMPLETE, admitted_count, target_count
        ) is True
        # ...so acquisition returns no items, deferral, or failures,
        assert result.items == []
        assert result.defer_reason is None
        assert result.provider_failures == []
        assert result.normalization_failures == []
        # ...no provider was ever invoked,
        assert spy.search_called is False
        # ...and no charge was ever attempted (zero Spend_Budget consumed).
        assert charges == []
