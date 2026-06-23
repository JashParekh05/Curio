"""Unit tests for the Game_Service best-effort Supabase persistence (Req 16.2, 16.3).

The three persistence helpers in ``app.services.game`` -- ``persist_session``,
``persist_progress``, and ``persist_attempt`` -- write to the Phase 2 tables
``game_sessions`` / ``game_progress`` / ``attempts`` through the reused
``db/supabase.get_client()`` accessor, each call wrapped in try/except as
best-effort (Req 16.2). On success they perform the documented upsert/insert and
return ``True``; on a Supabase failure they MUST log the failure, swallow the
exception (never raise), and return ``False`` so the learning loop is never
blocked (Req 16.3).

The helpers call ``supabase.get_client()`` where ``supabase`` is the module bound
in ``game``'s namespace (``from app.db import supabase``), so the name patched
here is ``game.supabase.get_client`` -- both the success double (the shared
``FakeDB`` from ``conftest``) and the failure injector are installed there.
"""

import logging

import pytest

from app.services import game
from tests.conftest import FakeDB

_SESSION = "sess-1"
_USER = "user-1"


def _use_db(monkeypatch, db):
    """Point the helpers' ``supabase.get_client()`` at the supplied double."""
    monkeypatch.setattr(game.supabase, "get_client", lambda: db)


def _raise_client(monkeypatch, exc=RuntimeError("supabase unreachable")):
    """Make ``supabase.get_client()`` raise, simulating a Supabase outage."""
    def _boom():
        raise exc

    monkeypatch.setattr(game.supabase, "get_client", _boom)


# ---------------------------------------------------------------------------
# Success path: the right table + insert/upsert is issued and the helper
# returns True.
# ---------------------------------------------------------------------------

class TestPersistSuccess:
    def test_persist_session_upserts_game_sessions_and_returns_true(self, monkeypatch):
        db = FakeDB(store={})
        _use_db(monkeypatch, db)

        result = game.persist_session(_SESSION, _USER, goal="backtracking", floor_node="loops")

        assert result is True
        assert len(db.rec["upserts"]) == 1
        table, payload = db.rec["upserts"][0]
        assert table == "game_sessions"
        assert payload["session_id"] == _SESSION
        assert payload["user_id"] == _USER
        assert payload["goal"] == "backtracking"
        assert payload["floor_node"] == "loops"

    def test_persist_progress_upserts_game_progress_and_returns_true(self, monkeypatch):
        db = FakeDB(store={})
        _use_db(monkeypatch, db)

        result = game.persist_progress(_SESSION, _USER, node="loops", state="CLEARED")

        assert result is True
        assert len(db.rec["upserts"]) == 1
        table, payload = db.rec["upserts"][0]
        assert table == "game_progress"
        assert payload["session_id"] == _SESSION
        assert payload["node"] == "loops"
        assert payload["state"] == "CLEARED"

    def test_persist_attempt_inserts_attempts_and_returns_true(self, monkeypatch):
        db = FakeDB(store={})
        _use_db(monkeypatch, db)

        result = game.persist_attempt(
            _SESSION,
            _USER,
            node="loops",
            band="CLIMB",
            score_pct=0.83,
            missed=["recursion"],
            correct=["iteration"],
        )

        assert result is True
        assert len(db.rec["inserts"]) == 1
        table, payload = db.rec["inserts"][0]
        assert table == "attempts"
        assert payload["session_id"] == _SESSION
        assert payload["node"] == "loops"
        assert payload["band"] == "CLIMB"
        assert payload["score_pct"] == 0.83
        assert payload["missed"] == ["recursion"]
        assert payload["correct"] == ["iteration"]


# ---------------------------------------------------------------------------
# Failure path: a simulated Supabase failure is logged, swallowed (no raise),
# and the helper returns False so the loop is never blocked (Req 16.2, 16.3).
# ---------------------------------------------------------------------------

class TestPersistBestEffortFailure:
    def test_persist_session_swallows_failure_logs_and_returns_false(
        self, monkeypatch, caplog
    ):
        _raise_client(monkeypatch)

        with caplog.at_level(logging.WARNING, logger="app.services.game"):
            result = game.persist_session(_SESSION, _USER, goal="backtracking")

        assert result is False  # no exception escaped; loop not blocked
        assert any(
            "persist_session" in r.getMessage() and r.levelno == logging.WARNING
            for r in caplog.records
        )

    def test_persist_progress_swallows_failure_logs_and_returns_false(
        self, monkeypatch, caplog
    ):
        _raise_client(monkeypatch)

        with caplog.at_level(logging.WARNING, logger="app.services.game"):
            result = game.persist_progress(_SESSION, _USER, node="loops", state="CLEARED")

        assert result is False
        assert any(
            "persist_progress" in r.getMessage() and r.levelno == logging.WARNING
            for r in caplog.records
        )

    def test_persist_attempt_swallows_failure_logs_and_returns_false(
        self, monkeypatch, caplog
    ):
        _raise_client(monkeypatch)

        with caplog.at_level(logging.WARNING, logger="app.services.game"):
            result = game.persist_attempt(
                _SESSION, _USER, node="loops", band="CLIMB", score_pct=0.9
            )

        assert result is False
        assert any(
            "persist_attempt" in r.getMessage() and r.levelno == logging.WARNING
            for r in caplog.records
        )

    def test_failure_during_execute_is_also_swallowed(self, monkeypatch):
        """A failure raised at query ``execute()`` time (not just at
        ``get_client()``) is handled identically: logged, swallowed, False."""
        db = FakeDB(store={}, fail={"game_sessions", "game_progress", "attempts"})
        _use_db(monkeypatch, db)

        assert game.persist_session(_SESSION, _USER, goal="g") is False
        assert game.persist_progress(_SESSION, _USER, node="n", state="NEXT") is False
        assert game.persist_attempt(_SESSION, _USER, node="n", band="DESCEND", score_pct=0.1) is False

    def test_failure_does_not_block_a_subsequent_persist(self, monkeypatch):
        """After a failed persist, a following best-effort write on a healthy
        client still lands -- the loop continues unblocked (Req 16.3)."""
        _raise_client(monkeypatch)
        assert game.persist_session(_SESSION, _USER, goal="g") is False

        db = FakeDB(store={})
        _use_db(monkeypatch, db)
        assert game.persist_session(_SESSION, _USER, goal="g") is True
        assert len(db.rec["upserts"]) == 1
