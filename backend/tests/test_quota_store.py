"""Integration tests for the fail-closed quota store (Task 8.2).

Exercises ``app.services.quota_store`` against a mocked Supabase client
(the shared ``FakeDB`` double from ``conftest``), verifying:

  - missing-row-implies-zero: ``load_today`` reports ``used = 0`` for a project
    with no row in the current window (a fresh rollover window);
  - atomic-increment charging: ``charge_and_persist`` invokes the
    ``increment_quota_usage`` RPC with the right ``(project, date, cost)``;
  - charge-before-call ordering: a successful persist records the increment and
    only then returns ``True`` (a recorded increment always precedes spend), and
    a non-positive cost spends nothing;
  - fail-closed behavior: a read error makes every project look fully used
    (``used == DAILY_QUOTA`` => unaffordable), and a write error makes
    ``charge_and_persist`` return ``False`` so the caller skips the API call.

Validates: Requirements 6.3, 6.4, 6.7
"""
from datetime import datetime, timezone

import pytest

from app.services import quota_store
from app.services.quota_pool import DAILY_QUOTA, can_afford, quota_window_date, remaining
from tests.conftest import FakeDB

# A fixed instant so the Pacific-time window date is deterministic across runs.
_NOW = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
_WINDOW = quota_window_date(_NOW).isoformat()
_TABLE = "project_quota_usage"
_RPC = "increment_quota_usage"


@pytest.fixture
def two_projects(monkeypatch):
    """Configure two operator-provisioned projects via YT_PROJECTS."""
    monkeypatch.setenv(
        "YT_PROJECTS",
        "projA:AIzaKeyAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA,"
        "projB:AIzaKeyBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
    )
    return ["projA", "projB"]


def _use_db(monkeypatch, db):
    """Point quota_store at the supplied fake client.

    quota_store does ``from app.db.supabase import get_client``, so the bound
    name to patch lives in the quota_store module namespace.
    """
    monkeypatch.setattr(quota_store, "get_client", lambda: db)


class _FailingRPC:
    """RPC handle whose execute() raises, to exercise the write-error path."""

    def execute(self):
        raise RuntimeError("rpc down")


class _RpcFailDB(FakeDB):
    """FakeDB whose rpc() always fails on execute()."""

    def rpc(self, name, params):
        return _FailingRPC()


# --------------------------------------------------------------------------
# load_today: missing-row-implies-zero and existing rows
# --------------------------------------------------------------------------

class TestLoadToday:
    def test_missing_row_implies_zero(self, monkeypatch, two_projects):
        # No rows persisted for the window => fresh window => used == 0.
        _use_db(monkeypatch, FakeDB(store={}))
        result = quota_store.load_today(now_utc=_NOW)
        assert [(p.project_id, p.used) for p in result] == [
            ("projA", 0),
            ("projB", 0),
        ]
        # used == 0 means the full daily budget remains.
        assert all(remaining(p) == DAILY_QUOTA for p in result)

    def test_reads_existing_used_units_missing_project_is_zero(
        self, monkeypatch, two_projects
    ):
        db = FakeDB(
            store={
                _TABLE: [
                    {"project_id": "projA", "quota_date": _WINDOW, "used_units": 500},
                ]
            }
        )
        _use_db(monkeypatch, db)
        result = quota_store.load_today(now_utc=_NOW)
        by_id = {p.project_id: p.used for p in result}
        assert by_id == {"projA": 500, "projB": 0}

    def test_ignores_rows_from_a_different_window(self, monkeypatch, two_projects):
        # A row dated to a different window must not count toward today's usage.
        db = FakeDB(
            store={
                _TABLE: [
                    {"project_id": "projA", "quota_date": "2023-12-31", "used_units": 9999},
                ]
            }
        )
        _use_db(monkeypatch, db)
        result = quota_store.load_today(now_utc=_NOW)
        assert {p.project_id: p.used for p in result} == {"projA": 0, "projB": 0}

    def test_used_units_clamped_to_daily_quota(self, monkeypatch, two_projects):
        db = FakeDB(
            store={
                _TABLE: [
                    {"project_id": "projA", "quota_date": _WINDOW, "used_units": 99999},
                ]
            }
        )
        _use_db(monkeypatch, db)
        result = quota_store.load_today(now_utc=_NOW)
        assert {p.project_id: p.used for p in result}["projA"] == DAILY_QUOTA

    def test_no_configured_projects_returns_empty(self, monkeypatch):
        monkeypatch.delenv("YT_PROJECTS", raising=False)
        monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
        _use_db(monkeypatch, FakeDB(store={}))
        assert quota_store.load_today(now_utc=_NOW) == []


# --------------------------------------------------------------------------
# load_today: fail closed on read error
# --------------------------------------------------------------------------

class TestLoadTodayFailsClosed:
    def test_read_error_marks_every_project_fully_used(self, monkeypatch, two_projects):
        # The store raises on read => every project must look fully used.
        _use_db(monkeypatch, FakeDB(store={}, fail={_TABLE}))
        result = quota_store.load_today(now_utc=_NOW)
        assert [p.project_id for p in result] == ["projA", "projB"]
        assert all(p.used == DAILY_QUOTA for p in result)

    def test_read_error_makes_all_projects_unaffordable(self, monkeypatch, two_projects):
        _use_db(monkeypatch, FakeDB(store={}, fail={_TABLE}))
        result = quota_store.load_today(now_utc=_NOW)
        # remaining == 0 => not even the cheapest call is affordable.
        assert all(remaining(p) == 0 for p in result)
        assert all(not can_afford(p, 1) for p in result)


# --------------------------------------------------------------------------
# charge_and_persist: atomic increment + charge-before-call ordering
# --------------------------------------------------------------------------

class TestChargeAndPersist:
    def test_calls_increment_rpc_with_project_date_cost(self, monkeypatch, two_projects):
        db = FakeDB(store={})
        _use_db(monkeypatch, db)
        ok = quota_store.charge_and_persist("projA", 100, now_utc=_NOW)
        assert ok is True
        # Exactly one atomic increment, carrying the right params.
        calls = db.rpc_named(_RPC)
        assert calls == [
            {"p_project": "projA", "p_date": _WINDOW, "p_cost": 100}
        ]

    def test_increment_is_recorded_before_true_return(self, monkeypatch, two_projects):
        # Charge-before-call: a True return implies the increment was durably
        # recorded, so a persisted charge always precedes the caller's spend.
        db = FakeDB(store={})
        _use_db(monkeypatch, db)
        ok = quota_store.charge_and_persist("projA", 1, now_utc=_NOW)
        assert ok is True
        assert len(db.rec["rpcs"]) == 1

    def test_metadata_cost_is_persisted_verbatim(self, monkeypatch, two_projects):
        db = FakeDB(store={})
        _use_db(monkeypatch, db)
        assert quota_store.charge_and_persist("projB", 1, now_utc=_NOW) is True
        assert db.rpc_named(_RPC) == [
            {"p_project": "projB", "p_date": _WINDOW, "p_cost": 1}
        ]

    def test_nonpositive_cost_spends_nothing(self, monkeypatch, two_projects):
        # Fail closed: a non-positive cost must not record any increment.
        db = FakeDB(store={})
        _use_db(monkeypatch, db)
        assert quota_store.charge_and_persist("projA", 0, now_utc=_NOW) is False
        assert quota_store.charge_and_persist("projA", -5, now_utc=_NOW) is False
        assert db.rec["rpcs"] == []

    def test_empty_project_spends_nothing(self, monkeypatch, two_projects):
        db = FakeDB(store={})
        _use_db(monkeypatch, db)
        assert quota_store.charge_and_persist("", 100, now_utc=_NOW) is False
        assert db.rec["rpcs"] == []


# --------------------------------------------------------------------------
# charge_and_persist: fail closed on write error
# --------------------------------------------------------------------------

class TestChargeAndPersistFailsClosed:
    def test_write_error_returns_false(self, monkeypatch, two_projects):
        # The increment RPC raises => no spend should follow => return False.
        _use_db(monkeypatch, _RpcFailDB(store={}))
        assert quota_store.charge_and_persist("projA", 100, now_utc=_NOW) is False
