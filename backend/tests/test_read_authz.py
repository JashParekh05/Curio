"""Integration tests for the read API in app/api/analytics.py — the read-only
Session_Journey and Engagement_Rollup endpoints (`get_session_journey`,
`get_user_journey`, `get_engagement_rollup`).

These drive the real async handlers directly (no HTTP) via `asyncio.run`,
mirroring the async-handler driving pattern of tests/test_serve_hook.py. The
authorization decision and the read-only guarantee are the contracts under test,
so the pure cores run for real while the I/O boundary is either mocked
(`is_operator`, the `fetch_*` helpers, the session-owner lookup) or backed by a
recording `FakeDB` that proves no write method is ever invoked.

Two contracts are isolated:

  * Read authorization (Req 5.1-5.7): an operator may read any session/user
    journey and a cross-user / all-users rollup; a non-operator is confined to
    the sessions/users it owns and to a self-scoped rollup — any cross-scope read
    is denied 403 with no data; `require_user` yields 401 when unauthenticated; an
    unsupported rollup dimension surfaces as 400.
  * Read-only guarantee (Req 5.8): backed by a recording FakeDB, the journey and
    rollup handlers invoke NO insert/update/delete — only reads.

Validates: Requirements 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8
"""
import asyncio

import pytest
from fastapi import HTTPException

import app.api.analytics as analytics
from app.api.analytics import (
    get_session_journey,
    get_user_journey,
    get_engagement_rollup,
)
from app.auth import require_user
from app.models.schemas import Impression
from tests.conftest import FakeDB


def _impression(clip_id="c0", *, session_id="s1", user_id="caller", feed_position=0):
    """A minimal valid Impression for journey/rollup construction."""
    return Impression(
        id=f"imp-{clip_id}",
        clip_id=clip_id,
        session_id=session_id,
        user_id=user_id,
        feed_surface="learn_path",
        feed_position=feed_position,
        pedagogical_role="definition",
        content_level="beginner",
        source_platform="youtube",
        topic_slug="t",
        served_at="2024-01-01T00:00:00+00:00",
    )


def _patch_fetchers(monkeypatch, *, session=None, user=None, all_impressions=None):
    """Stub the read-only I/O helpers in the analytics namespace so the handlers
    run the real pure cores over fixed Impression sets and never touch a DB.
    `fetch_watch_events` returns [] (every Impression renders as a skip)."""
    monkeypatch.setattr(analytics, "fetch_impressions_for_session", lambda sid: list(session or []))
    monkeypatch.setattr(analytics, "fetch_impressions_for_user", lambda uid: list(user or []))
    monkeypatch.setattr(analytics, "fetch_all_impressions", lambda: list(all_impressions or []))
    monkeypatch.setattr(analytics, "fetch_watch_events", lambda sids, cids: [])


# --------------------------------------------------------------------------- #
# Operator reads (Req 5.1, 5.2, 5.3, 5.4)
# --------------------------------------------------------------------------- #

class TestOperatorReads:
    def test_operator_reads_any_session_journey(self, monkeypatch):
        # An operator bypasses the ownership check and reads a session owned by
        # someone else (Req 5.1, 5.3).
        monkeypatch.setattr(analytics, "is_operator", lambda uid: True)
        _patch_fetchers(monkeypatch, session=[_impression("c0", user_id="someone-else")])

        result = asyncio.run(get_session_journey("s1", caller_id="operator"))

        assert result["session_id"] == "s1"
        assert len(result["journey"]) == 1

    def test_operator_reads_any_user_journey(self, monkeypatch):
        # An operator reads a cross-user journey (user_id != caller) (Req 5.4).
        monkeypatch.setattr(analytics, "is_operator", lambda uid: True)
        _patch_fetchers(monkeypatch, user=[_impression("c0", user_id="other"),
                                           _impression("c1", user_id="other", feed_position=1)])

        result = asyncio.run(get_user_journey("other", caller_id="operator"))

        assert result["user_id"] == "other"
        assert len(result["journey"]) == 2

    def test_operator_reads_cross_user_rollup(self, monkeypatch):
        # An operator scopes a rollup to a specific other user (Req 5.2).
        monkeypatch.setattr(analytics, "is_operator", lambda uid: True)
        _patch_fetchers(monkeypatch, user=[_impression("c0", user_id="other")])

        result = asyncio.run(get_engagement_rollup("feed_surface", caller_id="operator", user_id="other"))

        assert result["user_id"] == "other"
        assert result["dimension"] == "feed_surface"
        # One Impression -> one feed_surface group with the whole count.
        assert sum(g["count"] for g in result["rollup"]) == 1

    def test_operator_reads_all_users_rollup(self, monkeypatch):
        # An operator omits user_id for an unscoped, all-users aggregate backed by
        # fetch_all_impressions (Req 5.2).
        monkeypatch.setattr(analytics, "is_operator", lambda uid: True)
        called = {"all": 0}

        def fetch_all():
            called["all"] += 1
            return [_impression("c0", user_id="u1"), _impression("c1", user_id="u2", feed_position=1)]

        monkeypatch.setattr(analytics, "fetch_all_impressions", fetch_all)
        monkeypatch.setattr(analytics, "fetch_impressions_for_user", lambda uid: pytest.fail("must not scope to a user"))
        monkeypatch.setattr(analytics, "fetch_watch_events", lambda sids, cids: [])

        result = asyncio.run(get_engagement_rollup("user_id", caller_id="operator", user_id=None))

        assert called["all"] == 1
        assert result["user_id"] is None
        assert sum(g["count"] for g in result["rollup"]) == 2


# --------------------------------------------------------------------------- #
# Non-operator denials (Req 5.4, 5.5, 5.6, 5.7)
# --------------------------------------------------------------------------- #

class TestNonOperatorDenied:
    def test_non_operator_other_user_journey_denied(self, monkeypatch):
        # A non-operator reading another user's journey is denied 403 with no data
        # (Req 5.4, 5.7).
        monkeypatch.setattr(analytics, "is_operator", lambda uid: False)
        _patch_fetchers(monkeypatch, user=[_impression("c0", user_id="other")])

        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_user_journey("other", caller_id="caller"))

        assert exc.value.status_code == 403

    def test_non_operator_other_session_journey_denied(self, monkeypatch):
        # A non-operator reading a session it does not own (owner != caller) is
        # denied 403 with no data (Req 5.3, 5.6, 5.7).
        monkeypatch.setattr(analytics, "is_operator", lambda uid: False)
        monkeypatch.setattr(analytics, "_session_owner", lambda sid: "owner")
        # Fetchers would yield data if reached — proves denial happens first.
        _patch_fetchers(monkeypatch, session=[_impression("c0")])

        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_session_journey("s1", caller_id="attacker"))

        assert exc.value.status_code == 403

    def test_non_operator_cross_user_rollup_denied(self, monkeypatch):
        # A non-operator requesting a rollup scoped to another user is denied 403
        # (Req 5.5, 5.7).
        monkeypatch.setattr(analytics, "is_operator", lambda uid: False)
        _patch_fetchers(monkeypatch, user=[_impression("c0", user_id="other")])

        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_engagement_rollup("feed_surface", caller_id="caller", user_id="other"))

        assert exc.value.status_code == 403


# --------------------------------------------------------------------------- #
# Non-operator self-scoped reads succeed (Req 5.5, 5.6)
# --------------------------------------------------------------------------- #

class TestNonOperatorSelfScoped:
    def test_non_operator_reads_own_session_journey(self, monkeypatch):
        # A non-operator reading a session it OWNS (owner == caller) succeeds
        # (Req 5.6).
        monkeypatch.setattr(analytics, "is_operator", lambda uid: False)
        monkeypatch.setattr(analytics, "_session_owner", lambda sid: "caller")
        _patch_fetchers(monkeypatch, session=[_impression("c0", user_id="caller")])

        result = asyncio.run(get_session_journey("s1", caller_id="caller"))

        assert result["session_id"] == "s1"
        assert len(result["journey"]) == 1

    def test_non_operator_self_rollup_unscoped_narrows_to_caller(self, monkeypatch):
        # A non-operator omitting user_id is narrowed to its OWN impressions — the
        # all-users fetch is never used (Req 5.5).
        monkeypatch.setattr(analytics, "is_operator", lambda uid: False)
        seen = {"user_id": None}

        def fetch_user(uid):
            seen["user_id"] = uid
            return [_impression("c0", user_id=uid)]

        monkeypatch.setattr(analytics, "fetch_impressions_for_user", fetch_user)
        monkeypatch.setattr(analytics, "fetch_all_impressions", lambda: pytest.fail("must not read all users"))
        monkeypatch.setattr(analytics, "fetch_watch_events", lambda sids, cids: [])

        result = asyncio.run(get_engagement_rollup("feed_surface", caller_id="caller", user_id=None))

        assert seen["user_id"] == "caller"      # narrowed to the caller
        assert result["user_id"] == "caller"
        assert sum(g["count"] for g in result["rollup"]) == 1

    def test_non_operator_self_rollup_explicit_self_succeeds(self, monkeypatch):
        # A non-operator passing user_id == caller is allowed (Req 5.5).
        monkeypatch.setattr(analytics, "is_operator", lambda uid: False)
        _patch_fetchers(monkeypatch, user=[_impression("c0", user_id="caller")])

        result = asyncio.run(get_engagement_rollup("feed_surface", caller_id="caller", user_id="caller"))

        assert result["user_id"] == "caller"
        assert sum(g["count"] for g in result["rollup"]) == 1


# --------------------------------------------------------------------------- #
# Unauthenticated -> 401, and unsupported dimension -> 400
# --------------------------------------------------------------------------- #

class TestEdgeCases:
    def test_unauthenticated_require_user_raises_401(self):
        # The handlers depend on require_user, which raises 401 when the
        # Authorization header is missing or malformed (Req 5.1).
        with pytest.raises(HTTPException) as exc:
            require_user(authorization=None)
        assert exc.value.status_code == 401

        with pytest.raises(HTTPException) as exc:
            require_user(authorization="NotBearer xyz")
        assert exc.value.status_code == 401

    def test_handlers_depend_on_require_user(self):
        # Defense-in-depth: each read handler wires require_user as its caller_id
        # dependency, so an unauthenticated request never reaches the body.
        import inspect

        for handler in (get_session_journey, get_user_journey, get_engagement_rollup):
            default = inspect.signature(handler).parameters["caller_id"].default
            assert default.dependency is require_user

    def test_unsupported_rollup_dimension_returns_400(self, monkeypatch):
        # An unsupported Slice_Dimension makes the pure compute_rollup raise
        # ValueError, surfaced as HTTP 400 (Req 4.5).
        monkeypatch.setattr(analytics, "is_operator", lambda uid: True)
        _patch_fetchers(monkeypatch, all_impressions=[_impression("c0")])

        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_engagement_rollup("not_a_dimension", caller_id="operator", user_id=None))

        assert exc.value.status_code == 400


# --------------------------------------------------------------------------- #
# Read-only guarantee (Req 5.8)
# --------------------------------------------------------------------------- #

class TestReadOnlyGuarantee:
    """With the real fetch_* helpers running over a recording FakeDB, the handlers
    must perform only reads — no insert/update/delete is ever issued."""

    def _wire_real_io(self, monkeypatch, db):
        # Point BOTH the analytics session-owner lookup and the impression_store
        # fetchers at the same recording FakeDB so every DB touch is captured.
        import app.services.impression_store as store
        monkeypatch.setattr(analytics, "get_client", lambda: db)
        monkeypatch.setattr(store, "get_client", lambda: db)

    def test_session_journey_issues_no_writes(self, monkeypatch):
        monkeypatch.setattr(analytics, "is_operator", lambda uid: True)
        db = FakeDB(store={
            "impressions": [_impression("c0").model_dump()],
            "clip_events": [],
            "clips": [],
            "learning_paths": [{"session_id": "s1", "user_id": "caller"}],
        })
        self._wire_real_io(monkeypatch, db)

        result = asyncio.run(get_session_journey("s1", caller_id="operator"))

        assert result["session_id"] == "s1"
        assert db.rec["inserts"] == []
        assert db.rec["upserts"] == []

    def test_user_journey_issues_no_writes(self, monkeypatch):
        monkeypatch.setattr(analytics, "is_operator", lambda uid: True)
        db = FakeDB(store={
            "impressions": [_impression("c0", user_id="u1").model_dump()],
            "clip_events": [],
            "clips": [],
        })
        self._wire_real_io(monkeypatch, db)

        asyncio.run(get_user_journey("u1", caller_id="operator"))

        assert db.rec["inserts"] == []
        assert db.rec["upserts"] == []

    def test_rollup_issues_no_writes(self, monkeypatch):
        monkeypatch.setattr(analytics, "is_operator", lambda uid: True)
        db = FakeDB(store={
            "impressions": [
                _impression("c0", user_id="u1").model_dump(),
                _impression("c1", user_id="u2", feed_position=1).model_dump(),
            ],
            "clip_events": [],
            "clips": [],
        })
        self._wire_real_io(monkeypatch, db)

        result = asyncio.run(get_engagement_rollup("user_id", caller_id="operator", user_id=None))

        assert sum(g["count"] for g in result["rollup"]) == 2
        assert db.rec["inserts"] == []
        assert db.rec["upserts"] == []
