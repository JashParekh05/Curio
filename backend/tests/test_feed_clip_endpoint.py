"""Fake-DB tests for the single-clip metadata route `GET /api/feed/clip/{clip_id}`
in app/api/feed.py, exercised directly (no HTTP).

Follows the established backend convention (see tests/test_quiz_api.py and
tests/test_read_authz.py): the real async handler `get_clip` is driven directly
via `asyncio.run`, the Supabase boundary is replaced by the recording `FakeDB`
from tests/conftest.py by monkeypatching `feed.get_client`, and `require_user`
is bypassed by passing `caller_id` straight into the handler. Auth itself is
asserted the same way test_read_authz.py does: `require_user` raises 401
unauthenticated, and the handler wires `require_user` as its `caller_id`
dependency.

Covers:
  * 200 — a known clip_id returns the Clip (returned id matches).
  * 404 — a missing clip_id (empty data) raises HTTPException 404.
  * 503 — a DB error surfaces as HTTPException 503 (error path).
  * Auth — require_user gates the route (401 unauthenticated + dependency wiring).
  * Route ordering — `/api/feed/clip/{clip_id}` resolves to `get_clip` and is
    NOT shadowed by `/{topic_slug}`.
"""
import asyncio
import inspect

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from starlette.routing import Match

import app.api.feed as feed
from app.api.feed import get_clip, get_feed
from app.auth import require_user
from tests.conftest import FakeDB


def _patch_db(monkeypatch, store, fail=None):
    db = FakeDB(store=store, fail=fail or set())
    monkeypatch.setattr(feed, "get_client", lambda: db)
    return db


def _clip_row(clip_id="clip-1", **overrides):
    """A clips-table row with the columns get_clip selects, sufficient to
    construct a valid Clip."""
    row = {
        "id": clip_id,
        "topic_slug": "binary-search",
        "title": "What is binary search?",
        "description": "An intro clip",
        "video_url": "https://example.com/v.mp4",
        "thumbnail_url": "https://example.com/t.jpg",
        "duration_seconds": 42,
        "source_url": "https://youtube.com/watch?v=abc",
        "source_platform": "youtube",
        "hook_score": 0.77,
        "created_at": "2024-01-01T00:00:00+00:00",
        "section_index": 0,
    }
    row.update(overrides)
    return row


class TestGetClipSuccess:
    def test_known_clip_returns_clip(self, monkeypatch):
        # 200: a known clip_id returns the Clip with the matching id and columns.
        _patch_db(monkeypatch, {"clips": [_clip_row("clip-42")]})

        result = asyncio.run(get_clip("clip-42", caller_id="u"))

        assert result.id == "clip-42"
        assert result.topic_slug == "binary-search"
        assert result.title == "What is binary search?"
        assert result.hook_score == 0.77

    def test_selects_the_requested_clip(self, monkeypatch):
        # With multiple rows present, the eq("id", ...) filter selects exactly
        # the requested one.
        _patch_db(monkeypatch, {"clips": [
            _clip_row("clip-a", title="A"),
            _clip_row("clip-b", title="B"),
        ]})

        result = asyncio.run(get_clip("clip-b", caller_id="u"))

        assert result.id == "clip-b"
        assert result.title == "B"

    def test_missing_hook_score_defaults(self, monkeypatch):
        # The handler defaults hook_score when the column is absent.
        row = _clip_row("clip-nh")
        row.pop("hook_score")
        _patch_db(monkeypatch, {"clips": [row]})

        result = asyncio.run(get_clip("clip-nh", caller_id="u"))

        assert result.hook_score == 0.5


class TestGetClipMissing:
    def test_missing_clip_returns_404(self, monkeypatch):
        # 404: no row matches the id -> empty data -> HTTPException 404.
        _patch_db(monkeypatch, {"clips": [_clip_row("clip-1")]})

        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_clip("does-not-exist", caller_id="u"))

        assert exc.value.status_code == 404

    def test_empty_table_returns_404(self, monkeypatch):
        _patch_db(monkeypatch, {"clips": []})

        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_clip("clip-1", caller_id="u"))

        assert exc.value.status_code == 404


class TestGetClipDbError:
    def test_db_failure_returns_503(self, monkeypatch):
        # 503: a DB error on the clips query surfaces as HTTPException 503.
        _patch_db(monkeypatch, {"clips": [_clip_row("clip-1")]}, fail={"clips"})

        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_clip("clip-1", caller_id="u"))

        assert exc.value.status_code == 503


class TestAuth:
    def test_unauthenticated_require_user_raises_401(self):
        # require_user (the route's auth gate) raises 401 when the Authorization
        # header is missing or malformed.
        with pytest.raises(HTTPException) as exc:
            require_user(authorization=None)
        assert exc.value.status_code == 401

        with pytest.raises(HTTPException) as exc:
            require_user(authorization="NotBearer xyz")
        assert exc.value.status_code == 401

    def test_get_clip_depends_on_require_user(self):
        # The route gates on require_user: its caller_id parameter defaults to a
        # Depends(require_user), so an unauthenticated request never reaches the
        # body.
        default = inspect.signature(get_clip).parameters["caller_id"].default
        assert default.dependency is require_user


class TestRouteOrdering:
    def _api_routes(self):
        return [r for r in feed.router.routes if isinstance(r, APIRoute)]

    def test_clip_route_declared_before_topic_slug(self):
        # The literal `clip/{clip_id}` route must be registered BEFORE the
        # catch-all `/{topic_slug}` route, otherwise the topic handler would
        # shadow it.
        paths = [r.path for r in self._api_routes()]
        clip_path = "/api/feed/clip/{clip_id}"
        topic_path = "/api/feed/{topic_slug}"

        assert clip_path in paths
        assert topic_path in paths
        assert paths.index(clip_path) < paths.index(topic_path)

    def test_clip_url_resolves_to_get_clip_not_topic(self):
        # A GET to /api/feed/clip/SOMEID matches get_clip — the first matching
        # route in declaration order — and NOT the /{topic_slug} handler.
        scope = {"type": "http", "method": "GET", "path": "/api/feed/clip/SOMEID"}
        matched = []
        for route in self._api_routes():
            match, _ = route.matches(scope)
            if match == Match.FULL:
                matched.append(route)

        assert matched, "no route matched /api/feed/clip/SOMEID"
        first = matched[0]
        assert first.endpoint is get_clip
        assert first.endpoint is not get_feed
