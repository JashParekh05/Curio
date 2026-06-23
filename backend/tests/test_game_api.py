"""Example tests for the Game_Router endpoints in app/api/game.py (Req 13).

These exercise the three game endpoints over real HTTP via FastAPI's
``TestClient``, with the underlying ``game_service`` functions mocked so the
tests isolate the *router* contract: the documented wire shapes
(SessionStartResponse / DecideResponse / NodeResponse), the fact that ``decide``
surfaces the code-computed band alongside the action, and that every endpoint is
guarded by ``require_user``.

The service layer (banding, grading, descent/climb safety, clip/transcript
fallbacks) is covered by its own unit/property tests; here it is replaced with
deterministic doubles via monkeypatch so the router mapping is the only thing
under test.

Validates: Requirements 13.3, 13.4, 13.5, 13.6
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.game as game_api
from app.api.game import router
from app.auth import require_user
from app.services import game as game_service


# --------------------------------------------------------------------------- #
# Fixtures: a minimal app mounting only the Game_Router, plus a TestClient that
# overrides require_user so the guest token is treated as a valid caller.
# --------------------------------------------------------------------------- #


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def app():
    return _make_app()


@pytest.fixture
def client(app):
    """TestClient with require_user overridden to a fixed guest caller, so
    handlers run as if the anonymous guest token had been accepted (Req 13.6)."""
    app.dependency_overrides[require_user] = lambda: "guest-user"
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# Service doubles
# --------------------------------------------------------------------------- #


def _probe_question(tag="arrays", level="core"):
    return {
        "question": "What is X?",
        "options": ["a", "b", "c", "d"],
        "correct_index": 1,
        "explanation": "because b",
        "concept_tag": tag,
        "level": level,
    }


def _probe_six():
    return [_probe_question(tag=f"c{i}", level="core") for i in range(6)]


def _clip_dict():
    return {
        "video_id": "abc123",
        "video_url": "https://www.youtube.com/embed/abc123",
        "title": "Short explainer",
        "channel_title": "Chan",
        "duration_seconds": 120,
        "has_caption": True,
        "view_count": 9001,
        "thumbnail_url": "https://img/thumb.jpg",
        "description": "desc",
    }


# --------------------------------------------------------------------------- #
# POST /api/game/session -> SessionStartResponse (Req 13.3)
# --------------------------------------------------------------------------- #


class TestSessionEndpoint:
    def test_returns_session_start_shape(self, monkeypatch, client):
        result = game_service.SessionStart(
            session_id="sess-1",
            goal="binary search",
            current_node="binary search",
            probe=_probe_six(),
        )
        monkeypatch.setattr(game_service, "start_session", lambda topic: result)

        resp = client.post("/api/game/session", json={"topic": "binary search"})

        assert resp.status_code == 200
        body = resp.json()
        # Exact documented SessionStartResponse keys.
        assert set(body) == {"session_id", "goal", "current_node", "probe"}
        assert body["session_id"] == "sess-1"
        assert body["goal"] == "binary search"
        assert body["current_node"] == "binary search"  # == goal at start (Req 1.1)
        assert len(body["probe"]) == 6  # exactly 6 (Req 2.1)
        q = body["probe"][0]
        assert set(q) == {
            "question", "options", "correct_index",
            "explanation", "concept_tag", "level",
        }

    def test_forwards_topic_to_service(self, monkeypatch, client):
        seen = {}

        def fake_start(topic):
            seen["topic"] = topic
            return game_service.SessionStart("s", topic, topic, _probe_six())

        monkeypatch.setattr(game_service, "start_session", fake_start)

        client.post("/api/game/session", json={"topic": "graphs"})

        assert seen["topic"] == "graphs"

    def test_blank_topic_rejected_400(self, monkeypatch, client):
        # Blank topic is rejected before the service is consulted (Req 1.3).
        monkeypatch.setattr(
            game_service, "start_session",
            lambda topic: pytest.fail("service must not be called for blank topic"),
        )
        resp = client.post("/api/game/session", json={"topic": "   "})
        assert resp.status_code == 400

    def test_probe_generation_failure_502(self, monkeypatch, client):
        def boom(topic):
            raise game_service.ProbeGenerationError("nope")

        monkeypatch.setattr(game_service, "start_session", boom)

        resp = client.post("/api/game/session", json={"topic": "x"})
        assert resp.status_code == 502
        assert resp.json()["detail"] == "probe_generation_failed"


# --------------------------------------------------------------------------- #
# POST /api/game/decide -> DecideResponse incl. code-computed band (Req 13.4)
# --------------------------------------------------------------------------- #


class TestDecideEndpoint:
    def _decide_body(self):
        return {
            "goal": "binary search",
            "current_node": "binary search",
            "path": ["binary search"],
            "questions": [_probe_question(tag="arrays")],
            "answers": [0],
        }

    def test_returns_decide_shape_with_band(self, monkeypatch, client):
        # The service double returns DESCEND as the *code-computed* band while the
        # action is REINFORCE — proving the endpoint surfaces band independently
        # of action (the band is computed in code, never chosen by the LLM).
        response = game_service.DecideResponse(
            action="REINFORCE",
            band="DESCEND",
            next_node="arrays",
            reached_goal=False,
            gap="missing arrays",
            diagnosis="you stumble on indexing",
            score_pct=0.3333,
            missed_concepts=["arrays"],
            correct_concepts=[],
            floor_node=None,
            descent_depth=1,
        )
        monkeypatch.setattr(game_service, "decide", lambda req: response)

        resp = client.post("/api/game/decide", json=self._decide_body())

        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {
            "action", "band", "next_node", "reached_goal", "gap",
            "diagnosis", "score_pct", "missed_concepts", "correct_concepts",
        }
        # band is present and distinct from action -> it is the code-computed band.
        assert body["band"] == "DESCEND"
        assert body["action"] == "REINFORCE"
        assert body["next_node"] == "arrays"
        assert body["score_pct"] == 0.3333
        assert body["missed_concepts"] == ["arrays"]
        assert body["diagnosis"] == "you stumble on indexing"

    def test_converts_wire_questions_to_service_request(self, monkeypatch, client):
        # The endpoint builds a service DecideRequest from the wire payload: the
        # ProbeQuestion models become plain dicts and path/answers pass through,
        # so the service can grade and band from the code path (Req 13.4).
        captured = {}

        def fake_decide(req):
            captured["req"] = req
            return game_service.DecideResponse(
                action="CLIMB", band="CLIMB", next_node=None, reached_goal=True,
                gap="", diagnosis="solid", score_pct=1.0,
                missed_concepts=[], correct_concepts=["arrays"],
                floor_node=None, descent_depth=0,
            )

        monkeypatch.setattr(game_service, "decide", fake_decide)

        resp = client.post("/api/game/decide", json=self._decide_body())

        assert resp.status_code == 200
        req = captured["req"]
        assert isinstance(req, game_service.DecideRequest)
        assert req.goal == "binary search"
        assert req.path == ["binary search"]
        assert req.answers == [0]
        assert isinstance(req.questions[0], dict)
        assert req.questions[0]["concept_tag"] == "arrays"

    def test_decide_failure_502(self, monkeypatch, client):
        def boom(req):
            raise game_service.DecideError("nope")

        monkeypatch.setattr(game_service, "decide", boom)

        resp = client.post("/api/game/decide", json=self._decide_body())
        assert resp.status_code == 502
        assert resp.json()["detail"] == "decide_failed"


# --------------------------------------------------------------------------- #
# POST /api/game/node -> NodeResponse (Req 13.5)
# --------------------------------------------------------------------------- #


class TestNodeEndpoint:
    def _node_body(self):
        return {"node": "arrays", "goal": "binary search"}

    def test_returns_node_shape_with_clip(self, monkeypatch, client):
        payload = game_service.NodePayload(
            node="arrays",
            hook="Think of an array as numbered lockers.",
            clip=_clip_dict(),
            quiz=[_probe_question(tag="arrays") for _ in range(3)],
        )
        monkeypatch.setattr(game_service, "deliver_node", lambda node, goal: payload)

        resp = client.post("/api/game/node", json=self._node_body())

        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"node", "hook", "clip", "quiz"}
        assert body["node"] == "arrays"
        assert body["hook"].startswith("Think of an array")
        assert len(body["quiz"]) == 3  # 3 validated MCQs (Req 11.3)
        clip = body["clip"]
        assert set(clip) == {
            "video_id", "video_url", "title", "channel_title",
            "duration_seconds", "has_caption", "view_count",
            "thumbnail_url", "description",
        }
        # Canonical embed URL form (Req 10.3).
        assert clip["video_url"] == "https://www.youtube.com/embed/abc123"

    def test_returns_node_shape_with_null_clip(self, monkeypatch, client):
        # When no clip is affordable/available the node still delivers with a
        # null clip and a (transcript-free) quiz (Req 10.4).
        payload = game_service.NodePayload(
            node="arrays",
            hook="hook",
            clip=None,
            quiz=[_probe_question() for _ in range(3)],
        )
        monkeypatch.setattr(game_service, "deliver_node", lambda node, goal: payload)

        resp = client.post("/api/game/node", json=self._node_body())

        assert resp.status_code == 200
        body = resp.json()
        assert body["clip"] is None
        assert len(body["quiz"]) == 3

    def test_forwards_node_and_goal(self, monkeypatch, client):
        seen = {}

        def fake_deliver(node, goal):
            seen["node"], seen["goal"] = node, goal
            return game_service.NodePayload(node=node, hook="h", clip=None, quiz=[])

        monkeypatch.setattr(game_service, "deliver_node", fake_deliver)

        client.post("/api/game/node", json=self._node_body())

        assert seen == {"node": "arrays", "goal": "binary search"}


# --------------------------------------------------------------------------- #
# require_user enforcement across every endpoint (Req 13.6)
# --------------------------------------------------------------------------- #


class TestRequireUserEnforced:
    """Without the override (no/invalid Authorization header), every endpoint is
    rejected by require_user before the handler body runs. With the override the
    same request passes, confirming the guard is what gates access."""

    @pytest.fixture
    def unauthed_client(self, app):
        # No dependency override: the real require_user runs.
        with TestClient(app) as c:
            yield c

    def _guard_service(self, monkeypatch):
        # The service must never be reached on an unauthenticated request.
        monkeypatch.setattr(
            game_service, "start_session",
            lambda *a, **k: pytest.fail("start_session reached without auth"))
        monkeypatch.setattr(
            game_service, "decide",
            lambda *a, **k: pytest.fail("decide reached without auth"))
        monkeypatch.setattr(
            game_service, "deliver_node",
            lambda *a, **k: pytest.fail("deliver_node reached without auth"))

    def test_session_requires_auth(self, monkeypatch, unauthed_client):
        self._guard_service(monkeypatch)
        resp = unauthed_client.post("/api/game/session", json={"topic": "x"})
        assert resp.status_code in (401, 403)

    def test_decide_requires_auth(self, monkeypatch, unauthed_client):
        self._guard_service(monkeypatch)
        resp = unauthed_client.post("/api/game/decide", json={
            "goal": "g", "current_node": "g", "path": [],
            "questions": [], "answers": [],
        })
        assert resp.status_code in (401, 403)

    def test_node_requires_auth(self, monkeypatch, unauthed_client):
        self._guard_service(monkeypatch)
        resp = unauthed_client.post("/api/game/node", json={"node": "n", "goal": "g"})
        assert resp.status_code in (401, 403)

    def test_invalid_bearer_token_rejected(self, monkeypatch, unauthed_client):
        # A malformed Authorization header is rejected with 401 (Req 13.6).
        self._guard_service(monkeypatch)
        resp = unauthed_client.post(
            "/api/game/node",
            json={"node": "n", "goal": "g"},
            headers={"Authorization": "NotBearer xyz"},
        )
        assert resp.status_code == 401

    def test_override_allows_access(self, app, monkeypatch):
        # With require_user overridden, the same endpoint succeeds — proving the
        # guard is the gate (Req 13.6).
        app.dependency_overrides[require_user] = lambda: "guest-user"
        monkeypatch.setattr(
            game_service, "deliver_node",
            lambda node, goal: game_service.NodePayload(node, "h", None, []))
        with TestClient(app) as c:
            resp = c.post("/api/game/node", json={"node": "n", "goal": "g"})
        app.dependency_overrides.clear()
        assert resp.status_code == 200

    def test_handlers_declare_require_user_dependency(self):
        # Defense-in-depth: each handler wires require_user as its caller_id
        # dependency, so an unauthenticated request never reaches the body.
        import inspect

        for handler in (
            game_api.start_session, game_api.decide, game_api.deliver_node,
        ):
            default = inspect.signature(handler).parameters["caller_id"].default
            assert default.dependency is require_user
