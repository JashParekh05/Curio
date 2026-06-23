"""Unit tests for account-linked sessions and resume precedence (Req 17.1, 17.2).

Task 14.2. These cover the two account-linking guarantees Phase 2 adds on top of
the stateless Phase 1 loop:

- **Account linking (Req 17.1):** WHEN an authenticated learner starts a session,
  THE Game_Router associates the session with the caller's user id from
  ``require_user``. The ``POST /api/game/session`` handler calls
  ``game_service.persist_session(user_id=caller_id, ...)``; here ``persist_session``
  is patched to capture its arguments and the test asserts the persisted
  ``user_id`` is exactly the ``require_user`` caller id (the persistence side
  effect itself is covered by ``test_game_persistence.py``).

- **Resume precedence (Req 17.2):** WHERE persisted progress exists for a learner,
  THE Game_Service prefers that persisted progress over local-only state.
  ``prefer_persisted_progress`` returns ``source == "persisted"`` (built from the
  persisted ``ResumeState``) when a session row was found, and ``source ==
  "local"`` (the client's unchanged local-only state) when none was.

- **Resume miss (Req 16.4, 17.1):** ``load_resume_state`` returns
  ``found == False`` when no ``game_sessions`` row is persisted, so the caller
  keeps its local-only state rather than clobbering a brand-new/offline session.

The router tests follow ``test_game_api.py``: a minimal app mounting only the
Game_Router and a ``TestClient`` with ``require_user`` overridden to a fixed guest
caller. Supabase reads are driven through the shared ``FakeDB`` from ``conftest``,
patched onto ``game.supabase.get_client`` exactly as ``test_game_persistence.py``
does.

Validates: Requirements 17.1, 17.2
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.game import router
from app.auth import require_user
from app.services import game
from app.services import game as game_service
from tests.conftest import FakeDB


# --------------------------------------------------------------------------- #
# Fixtures (mirroring test_game_api.py)
# --------------------------------------------------------------------------- #

_CALLER_ID = "guest-user-42"


@pytest.fixture
def app():
    a = FastAPI()
    a.include_router(router)
    return a


@pytest.fixture
def client(app):
    """TestClient with require_user overridden to a fixed guest caller, so the
    handler runs as if the anonymous guest token had been accepted (Req 13.6)."""
    app.dependency_overrides[require_user] = lambda: _CALLER_ID
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _probe_six():
    return [
        {
            "question": f"Q{i}?",
            "options": ["a", "b", "c", "d"],
            "correct_index": 1,
            "explanation": "because b",
            "concept_tag": f"c{i}",
            "level": "core",
        }
        for i in range(6)
    ]


def _use_db(monkeypatch, db):
    """Point the resume reads' ``supabase.get_client()`` at the supplied double."""
    monkeypatch.setattr(game.supabase, "get_client", lambda: db)


# --------------------------------------------------------------------------- #
# (a) Account linking: /session persists the session keyed to the caller id
#     from require_user (Req 17.1).
# --------------------------------------------------------------------------- #


class TestSessionKeyedToCallerId:
    def test_session_persisted_with_require_user_caller_id(self, monkeypatch, client):
        # The probe-producing service call is stubbed so the test isolates the
        # account-linking behavior (the persisted user_id).
        monkeypatch.setattr(
            game_service,
            "start_session",
            lambda topic: game_service.SessionStart(
                session_id="sess-1",
                goal=topic,
                current_node=topic,
                probe=_probe_six(),
            ),
        )

        captured = {}

        def fake_persist(*, session_id, user_id, goal, floor_node=None):
            captured.update(
                session_id=session_id,
                user_id=user_id,
                goal=goal,
                floor_node=floor_node,
            )
            return True

        monkeypatch.setattr(game_service, "persist_session", fake_persist)

        resp = client.post("/api/game/session", json={"topic": "backtracking"})

        assert resp.status_code == 200
        # The session is associated with the caller's user id from require_user
        # (Req 17.1) -- not the topic, session id, or anything else.
        assert captured["user_id"] == _CALLER_ID
        assert captured["session_id"] == "sess-1"
        assert captured["goal"] == "backtracking"

    def test_persistence_failure_does_not_block_session_start(self, monkeypatch, client):
        # persist_session is best-effort: even if it reports a failed write, the
        # session still starts and returns its probe (the loop is never blocked).
        monkeypatch.setattr(
            game_service,
            "start_session",
            lambda topic: game_service.SessionStart(
                session_id="sess-2", goal=topic, current_node=topic, probe=_probe_six()
            ),
        )
        monkeypatch.setattr(game_service, "persist_session", lambda **kw: False)

        resp = client.post("/api/game/session", json={"topic": "graphs"})

        assert resp.status_code == 200
        assert resp.json()["session_id"] == "sess-2"
        assert len(resp.json()["probe"]) == 6


# --------------------------------------------------------------------------- #
# (b) Resume precedence: prefer_persisted_progress prefers persisted progress
#     when a session row was found, else keeps local-only state (Req 17.2).
# --------------------------------------------------------------------------- #


class TestPreferPersistedProgress:
    def test_prefers_persisted_when_session_row_found(self):
        persisted = game.ResumeState(
            found=True,
            session_id="sess-1",
            user_id=_CALLER_ID,
            goal="backtracking",
            floor_node="loops",
            nodes=(
                game.PersistedNode(
                    node="loops", state="CLEARED", decay_due_at=None, decay_step=0
                ),
            ),
        )
        local = {
            "session_id": "sess-1",
            "user_id": _CALLER_ID,
            "goal": "stale-local-goal",
            "floor_node": None,
            "nodes": [],
        }

        resolved = game.prefer_persisted_progress(persisted, local)

        # Persisted progress wins over local-only state (Req 17.2).
        assert resolved["source"] == "persisted"
        assert resolved["goal"] == "backtracking"
        assert resolved["floor_node"] == "loops"
        assert resolved["session_id"] == "sess-1"
        assert [n["node"] for n in resolved["nodes"]] == ["loops"]
        assert resolved["nodes"][0]["state"] == "CLEARED"

    def test_keeps_local_when_no_session_row_found(self):
        # found=False => nothing persisted; the client's local-only state is
        # returned unchanged so a brand-new/offline session is not clobbered.
        persisted = game.ResumeState(found=False, session_id="sess-1")
        local = {
            "session_id": "sess-1",
            "user_id": _CALLER_ID,
            "goal": "local-goal",
            "floor_node": "local-floor",
            "nodes": [
                {"node": "n1", "state": "NEXT", "decay_due_at": None, "decay_step": 0}
            ],
        }

        resolved = game.prefer_persisted_progress(persisted, local)

        assert resolved["source"] == "local"
        assert resolved["goal"] == "local-goal"
        assert resolved["floor_node"] == "local-floor"
        assert resolved["nodes"] == local["nodes"]

    def test_keeps_local_when_persisted_is_none(self):
        # A None persisted state (e.g. resume read degraded) also falls back to
        # local-only state without raising.
        local = {"session_id": "sess-9", "goal": "g", "floor_node": None, "nodes": []}

        resolved = game.prefer_persisted_progress(None, local)

        assert resolved["source"] == "local"
        assert resolved["session_id"] == "sess-9"
        assert resolved["goal"] == "g"


# --------------------------------------------------------------------------- #
# (c) load_resume_state returns found=False when no session row is persisted
#     (Req 16.4, 17.1).
# --------------------------------------------------------------------------- #


class TestLoadResumeState:
    def test_returns_found_false_when_no_row(self, monkeypatch):
        # Empty store -> load_session finds no game_sessions row -> found=False.
        _use_db(monkeypatch, FakeDB(store={}))

        state = game.load_resume_state("missing-session")

        assert state.found is False
        assert state.session_id == "missing-session"
        assert state.goal is None
        assert state.floor_node is None
        assert state.nodes == ()

    def test_returns_found_true_with_persisted_fields_when_row_exists(self, monkeypatch):
        # A persisted session row + per-node progress rehydrates a found state
        # keyed to the caller's user id (Req 17.1).
        store = {
            "game_sessions": [
                {
                    "session_id": "sess-1",
                    "user_id": _CALLER_ID,
                    "goal": "backtracking",
                    "floor_node": "loops",
                    "updated_at": "2024-01-01T00:00:00+00:00",
                }
            ],
            "game_progress": [
                {
                    "session_id": "sess-1",
                    "node": "loops",
                    "state": "CLEARED",
                    "decay_due_at": None,
                    "decay_step": 0,
                }
            ],
        }
        _use_db(monkeypatch, FakeDB(store=store))

        state = game.load_resume_state("sess-1")

        assert state.found is True
        assert state.session_id == "sess-1"
        assert state.user_id == _CALLER_ID
        assert state.goal == "backtracking"
        assert state.floor_node == "loops"
        assert [n.node for n in state.nodes] == ["loops"]

    def test_found_false_state_is_not_persisted_progress(self, monkeypatch):
        # has_persisted_progress agrees: a not-found state carries no usable
        # persisted progress, so a resume keeps local-only state (Req 17.2).
        _use_db(monkeypatch, FakeDB(store={}))

        state = game.load_resume_state("missing-session")

        assert game.has_persisted_progress(state) is False
