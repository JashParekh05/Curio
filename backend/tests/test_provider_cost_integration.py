"""Integration tests for independent provider budgets and Key_Pool reuse (Task 5.6).

Exercises the ``charge_before_call`` I/O shell in
``app.services.provider_cost`` against the shared ``FakeDB`` Supabase double and
monkeypatched ``Key_Pool`` seams (``quota_store`` / ``quota_pool``), verifying the
two cross-cutting accounting guarantees the pure core cannot express on its own:

  - **Independent per-provider budgets (Req 7.2).** Charging one non-YouTube
    provider reads and writes only that provider's own
    ``(provider_id, window_key)`` row in ``provider_spend``; a second provider's
    ``provider_spend`` row is never read or written, so providers never interfere.

  - **Key_Pool reuse for YouTube (Req 7.5).** A ``youtube`` charge routes through
    the existing per-project quota accounting (``quota_store.load_today`` +
    ``quota_pool.select_project`` + ``quota_store.charge_and_persist`` over
    ``project_quota_usage``) and creates NO ``provider_spend`` row, so no quota
    counter is duplicated.

Everything is offline: the Supabase client (``app.db.supabase.get_client``, which
``provider_cost`` imports lazily inside its I/O helpers) and the Key_Pool seams
are replaced by doubles, so no real service is touched.

Validates: Requirements 7.2, 7.5
"""
from datetime import datetime, timezone

import pytest

import app.db.supabase as supabase_mod
from app.services import provider_cost, quota_pool, quota_store
from app.services.content_provider import ProviderCapability
from app.services.quota_pool import DAILY_QUOTA, SEARCH_COST, ProjectQuota
from tests.conftest import FakeDB

# A fixed instant so the accounting-window key is deterministic across runs.
_NOW = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
_WINDOW = "2024-01-15"  # provider_cost._window_key uses the UTC calendar date.

_REGISTRY = "provider_registry"
_SPEND = "provider_spend"

# Two non-YouTube providers, each with its own search Cost_Unit and Spend_Budget.
_VIMEO_COST = 10
_PODCAST_COST = 7
_BUDGET = 100


def _registry_rows():
    """Two registered providers, each declaring a search Cost_Unit + Spend_Budget."""
    return [
        {
            "provider_id": "vimeo",
            "cost_policy": {
                "cost_units": {ProviderCapability.SEARCH.value: _VIMEO_COST},
                "spend_budget": _BUDGET,
            },
        },
        {
            "provider_id": "podcast",
            "cost_policy": {
                "cost_units": {ProviderCapability.SEARCH.value: _PODCAST_COST},
                "spend_budget": _BUDGET,
            },
        },
    ]


def _spend_rows():
    """An existing spend row per provider for the current window."""
    return [
        {"provider_id": "vimeo", "window_key": _WINDOW, "spent": 30, "budget": _BUDGET},
        {"provider_id": "podcast", "window_key": _WINDOW, "spent": 50, "budget": _BUDGET},
    ]


def _use_db(monkeypatch, db):
    """Point provider_cost's lazy ``get_client`` import at the supplied fake.

    ``provider_cost`` does ``from app.db.supabase import get_client`` inside its
    I/O helpers, so the name is resolved from ``app.db.supabase`` at call time;
    patching it there is what the helpers see.
    """
    monkeypatch.setattr(supabase_mod, "get_client", lambda: db)


# --------------------------------------------------------------------------
# Req 7.2: providers are accounted independently
# --------------------------------------------------------------------------

class TestIndependentBudgets:
    def test_charging_one_provider_writes_only_its_own_spend_row(self, monkeypatch):
        db = FakeDB(store={_REGISTRY: _registry_rows(), _SPEND: _spend_rows()})
        _use_db(monkeypatch, db)

        ok = provider_cost.charge_before_call(
            "vimeo", ProviderCapability.SEARCH, cache_hit=False, now_utc=_NOW
        )

        assert ok is True
        # Exactly one spend write, and it targets vimeo's own (provider_id,
        # window_key) key -- the charge persisted before the call (Req 7.1, 7.2).
        spend_upserts = [
            payload for table, payload in db.rec["upserts"] if table == _SPEND
        ]
        assert len(spend_upserts) == 1
        written = spend_upserts[0]
        assert written["provider_id"] == "vimeo"
        assert written["window_key"] == _WINDOW
        assert written["spent"] == 30 + _VIMEO_COST  # spent + search Cost_Unit

    def test_other_providers_spend_row_is_left_untouched(self, monkeypatch):
        db = FakeDB(store={_REGISTRY: _registry_rows(), _SPEND: _spend_rows()})
        _use_db(monkeypatch, db)

        provider_cost.charge_before_call(
            "vimeo", ProviderCapability.SEARCH, cache_hit=False, now_utc=_NOW
        )

        # No write ever touches podcast's key -- providers never interfere (Req 7.2).
        assert all(
            payload.get("provider_id") != "podcast"
            for table, payload in db.rec["upserts"]
        )
        # And podcast's persisted spend row is exactly as it started.
        podcast_row = next(
            r for r in db.store[_SPEND] if r["provider_id"] == "podcast"
        )
        assert podcast_row["spent"] == 50

    def test_independent_charges_each_hit_their_own_key(self, monkeypatch):
        db = FakeDB(store={_REGISTRY: _registry_rows(), _SPEND: _spend_rows()})
        _use_db(monkeypatch, db)

        assert provider_cost.charge_before_call(
            "vimeo", ProviderCapability.SEARCH, cache_hit=False, now_utc=_NOW
        )
        assert provider_cost.charge_before_call(
            "podcast", ProviderCapability.SEARCH, cache_hit=False, now_utc=_NOW
        )

        by_provider = {
            payload["provider_id"]: payload
            for table, payload in db.rec["upserts"]
            if table == _SPEND
        }
        # Each provider's charge tracked its own starting spend + its own
        # Cost_Unit, with no cross-contamination of budgets (Req 7.2).
        assert by_provider["vimeo"]["spent"] == 30 + _VIMEO_COST
        assert by_provider["podcast"]["spent"] == 50 + _PODCAST_COST


# --------------------------------------------------------------------------
# Req 7.5: YouTube reuses the Key_Pool and writes no provider_spend row
# --------------------------------------------------------------------------

class _ChargeSpy:
    """Records (project_id, cost) for every Key_Pool charge_and_persist call."""

    def __init__(self, ok=True):
        self.calls: list[tuple[str, int]] = []
        self.ok = ok

    def __call__(self, project_id, cost, now_utc=None):
        self.calls.append((project_id, cost))
        return self.ok


class TestYouTubeReusesKeyPool:
    @pytest.fixture
    def key_pool(self, monkeypatch):
        """Stub the Key_Pool seams with one affordable project; spy the charge."""
        load_today = lambda now=None: [ProjectQuota("projA", 0)]
        charge_spy = _ChargeSpy(ok=True)
        monkeypatch.setattr(quota_store, "load_today", load_today)
        monkeypatch.setattr(quota_store, "charge_and_persist", charge_spy)
        # quota_pool.select_project is pure; leave it real so selection is genuine.
        return charge_spy

    def test_youtube_search_charges_key_pool_not_provider_spend(
        self, monkeypatch, key_pool
    ):
        # A FakeDB whose every provider_spend touch is observable. If the youtube
        # path wrote a provider_spend row, it would show up here.
        get_client_spy = _ClientSpy(FakeDB(store={}))
        monkeypatch.setattr(supabase_mod, "get_client", get_client_spy)

        ok = provider_cost.charge_before_call(
            "youtube", ProviderCapability.SEARCH, cache_hit=False, now_utc=_NOW
        )

        assert ok is True
        # Routed through the Key_Pool: the 100-unit search charge landed on the
        # selected project via quota_store.charge_and_persist (Req 7.5).
        assert key_pool.calls == [("projA", SEARCH_COST)]
        # No provider_spend row created -- no quota counter duplicated (Req 7.5).
        assert get_client_spy.calls == 0
        assert get_client_spy.db.rec["upserts"] == []

    def test_youtube_exhausted_pool_fails_closed_without_provider_spend(
        self, monkeypatch
    ):
        # The only project is fully used -> nothing can afford a search.
        monkeypatch.setattr(
            quota_store, "load_today", lambda now=None: [ProjectQuota("projA", DAILY_QUOTA)]
        )
        charge_spy = _ChargeSpy(ok=True)
        monkeypatch.setattr(quota_store, "charge_and_persist", charge_spy)
        get_client_spy = _ClientSpy(FakeDB(store={}))
        monkeypatch.setattr(supabase_mod, "get_client", get_client_spy)

        ok = provider_cost.charge_before_call(
            "youtube", ProviderCapability.SEARCH, cache_hit=False, now_utc=_NOW
        )

        # Fail closed: no charge, and still no provider_spend row written.
        assert ok is False
        assert charge_spy.calls == []
        assert get_client_spy.calls == 0
        assert get_client_spy.db.rec["upserts"] == []

    def test_youtube_cache_hit_is_free_and_writes_no_provider_spend(
        self, monkeypatch, key_pool
    ):
        get_client_spy = _ClientSpy(FakeDB(store={}))
        monkeypatch.setattr(supabase_mod, "get_client", get_client_spy)

        ok = provider_cost.charge_before_call(
            "youtube", ProviderCapability.SEARCH, cache_hit=True, now_utc=_NOW
        )

        # Reuse before spend: cache hit costs nothing and touches neither the
        # Key_Pool nor provider_spend (Req 7.5, 7.6).
        assert ok is True
        assert key_pool.calls == []
        assert get_client_spy.calls == 0


class _ClientSpy:
    """Wraps a FakeDB and counts how many times get_client() is invoked."""

    def __init__(self, db):
        self.db = db
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.db
