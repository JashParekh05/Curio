"""Integration tests for the serve hook in app/api/feed.py — the best-effort,
non-blocking Impression write wired into `get_discover_feed` and `get_path_feed`
via `_schedule_impressions`.

These drive the real async handlers directly (no HTTP) against a faked Supabase
client, mirroring the fake-DB pattern of tests/test_discover_serving.py and
tests/test_feed_retrieval.py. The serving/ranking internals are stubbed (covered
by those suites) so each test isolates the serve hook's two contracts:

  * Non-blocking + best-effort (Req 2.1, 2.2, 2.3): a failing Impression write —
    whether the build raises on the request path or the insert raises later — never
    affects the already-prepared feed response, and the write is scheduled as a
    BackgroundTask rather than awaited.
  * Write authorization + user resolution (Req 2.4, 2.5, 2.6, 2.7): a serve that
    references another user's session is rejected (403) and schedules no Impression;
    a nonexistent session schedules no Impression; an owned session resolves the
    Impression user_id to the session owner; a discover serve resolves it to the
    caller.

Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7
"""
import asyncio

import pytest
from fastapi import HTTPException

import app.api.feed as feed_api
from app.api.feed import get_discover_feed, get_path_feed
from app.models.schemas import DiscoverResponse, FeedResponse
from tests.conftest import FakeDB, make_clip


class RecordingBackgroundTasks:
    """Stand-in for fastapi.BackgroundTasks that ONLY records scheduled tasks.

    It never runs them, so a returned response proves the handler scheduled the
    Impression write as a BackgroundTask rather than awaiting it — a write that
    raises can only blow up if it is actually executed, which this double never
    does."""

    def __init__(self):
        self.tasks: list[tuple] = []

    def add_task(self, func, *args, **kwargs):
        self.tasks.append((func, args, kwargs))


def _scheduled_impressions(bg: RecordingBackgroundTasks):
    """The impressions list handed to the scheduled record_impressions task, or
    None when no Impression write was scheduled."""
    for func, args, _ in bg.tasks:
        if func is feed_api.record_impressions:
            return args[0]
    return None


# --------------------------------------------------------------------------- #
# Discover serve hook
# --------------------------------------------------------------------------- #

def _patch_discover(monkeypatch, *, fetched_clips, interests=None):
    """Wire a fake DB + stubbed retrieval so the discover serving path runs in
    isolation. Empty interests keeps the Per_User_Topup seed task out of the way
    so bg.tasks holds only the Impression write."""
    db = FakeDB(store={
        "user_profiles": [{
            "user_id": "caller",
            "interests": interests if interests is not None else [],
            "taste_vector": None,
            "interest_vector": {},
            "grade_level": "high_school",
        }],
        "learning_paths": [],
        "topics": [{"slug": "t"}],
        "clips": [],
    })
    monkeypatch.setattr(feed_api, "get_client", lambda: db)
    monkeypatch.setattr(feed_api, "_match_interest_slugs", lambda *a, **k: ["t"])
    monkeypatch.setattr(feed_api, "_fetch_discover_clips", lambda *a, **k: list(fetched_clips))
    return db


class TestDiscoverServeHook:
    def test_build_failure_does_not_break_feed_and_schedules_nothing(self, monkeypatch):
        # The Impression build raises ON the request path. _schedule_impressions
        # swallows it: the feed is returned unchanged and no write is scheduled
        # (build failed before add_task) — Req 2.1.
        clips = [make_clip(id="c0"), make_clip(id="c1")]
        _patch_discover(monkeypatch, fetched_clips=clips)

        def boom(*a, **k):
            raise RuntimeError("build exploded")

        monkeypatch.setattr(feed_api, "build_impressions", boom)

        bg = RecordingBackgroundTasks()
        resp = asyncio.run(get_discover_feed("caller", bg, limit=20, caller_id="caller"))

        assert isinstance(resp, DiscoverResponse)
        assert [c.id for c in resp.clips] == ["c0", "c1"]   # feed unchanged
        assert _scheduled_impressions(bg) is None

    def test_write_is_scheduled_not_awaited(self, monkeypatch):
        # The insert itself raises, but it is scheduled as a BackgroundTask the
        # double never runs — so the handler returns the unchanged feed without
        # raising. If it were awaited, this RuntimeError would propagate
        # (Req 2.2, 2.3).
        clips = [make_clip(id="c0"), make_clip(id="c1")]
        _patch_discover(monkeypatch, fetched_clips=clips)

        def boom(*a, **k):
            raise RuntimeError("insert exploded")

        monkeypatch.setattr(feed_api, "record_impressions", boom)

        bg = RecordingBackgroundTasks()
        resp = asyncio.run(get_discover_feed("caller", bg, limit=20, caller_id="caller"))

        assert isinstance(resp, DiscoverResponse)
        assert [c.id for c in resp.clips] == ["c0", "c1"]
        # The write WAS scheduled (as a task), proving it was deferred not awaited.
        scheduled = _scheduled_impressions(bg)
        assert scheduled is not None and len(scheduled) == 2

    def test_discover_resolves_user_id_to_caller(self, monkeypatch):
        # A discover serve references no Session -> Impression user_id is the
        # authenticated caller, session_id is null (Req 2.7).
        clips = [make_clip(id="c0"), make_clip(id="c1")]
        _patch_discover(monkeypatch, fetched_clips=clips)

        bg = RecordingBackgroundTasks()
        resp = asyncio.run(get_discover_feed("caller", bg, limit=20, caller_id="caller"))

        assert isinstance(resp, DiscoverResponse)
        scheduled = _scheduled_impressions(bg)
        assert scheduled is not None and len(scheduled) == 2
        assert all(imp.user_id == "caller" for imp in scheduled)
        assert all(imp.session_id is None for imp in scheduled)
        assert all(imp.feed_surface == "discover" for imp in scheduled)

    def test_discover_uses_caller_not_path_user_id(self, monkeypatch):
        # Discover is self-only by construction: the path user_id is IGNORED in
        # favor of the authenticated caller, so a mismatched path param can never
        # expose another user's feed (self-lookup, no 403 needed — Req 2.4).
        _patch_discover(monkeypatch, fetched_clips=[make_clip(id="c0")])

        bg = RecordingBackgroundTasks()
        resp = asyncio.run(get_discover_feed("victim", bg, limit=20, caller_id="attacker"))

        assert isinstance(resp, DiscoverResponse)
        scheduled = _scheduled_impressions(bg)
        # Any scheduled impression is stamped with the CALLER, never the path param.
        assert scheduled is not None
        assert all(imp.user_id == "attacker" for imp in scheduled)
        assert all(imp.user_id != "victim" for imp in scheduled)


# --------------------------------------------------------------------------- #
# Path-feed serve hook
# --------------------------------------------------------------------------- #

def _patch_path(monkeypatch, *, owner, fetched_clips, session_id="s1"):
    """Wire a fake DB + stubbed serving internals so the path-feed serving path
    runs in isolation. Returns the FakeDB."""
    db = FakeDB(store={
        "learning_paths": [{
            "session_id": session_id,
            "user_id": owner,
            "topic_slugs": ["t"],
            "user_query": "q",
        }],
        "clip_events": [],
    })
    monkeypatch.setattr(feed_api, "get_client", lambda: db)
    monkeypatch.setattr(feed_api, "_get_session_telemetry", lambda *a, **k: (set(), {}))
    monkeypatch.setattr(feed_api, "_fetch_clips_for_slug", lambda *a, **k: list(fetched_clips))
    monkeypatch.setattr(feed_api, "_transcript_boost", lambda clips, q: clips)
    monkeypatch.setattr(feed_api, "_is_expansion_candidate", lambda *a, **k: False)
    monkeypatch.setattr(feed_api, "_should_extend", lambda *a, **k: False)
    return db


class TestPathServeHook:
    def test_build_failure_does_not_break_feed_and_schedules_nothing(self, monkeypatch):
        clips = [make_clip(id="c0", topic_slug="t"), make_clip(id="c1", topic_slug="t")]
        _patch_path(monkeypatch, owner="owner", fetched_clips=clips)

        def boom(*a, **k):
            raise RuntimeError("build exploded")

        monkeypatch.setattr(feed_api, "build_impressions", boom)

        bg = RecordingBackgroundTasks()
        feeds = asyncio.run(get_path_feed("s1", bg, caller_id="owner"))

        assert isinstance(feeds, list) and all(isinstance(f, FeedResponse) for f in feeds)
        served_ids = [c.id for f in feeds for c in f.clips]
        assert served_ids == ["c0", "c1"]            # feed unchanged
        assert _scheduled_impressions(bg) is None

    def test_write_is_scheduled_not_awaited(self, monkeypatch):
        clips = [make_clip(id="c0", topic_slug="t"), make_clip(id="c1", topic_slug="t")]
        _patch_path(monkeypatch, owner="owner", fetched_clips=clips)

        def boom(*a, **k):
            raise RuntimeError("insert exploded")

        monkeypatch.setattr(feed_api, "record_impressions", boom)

        bg = RecordingBackgroundTasks()
        feeds = asyncio.run(get_path_feed("s1", bg, caller_id="owner"))

        served_ids = [c.id for f in feeds for c in f.clips]
        assert served_ids == ["c0", "c1"]
        scheduled = _scheduled_impressions(bg)
        assert scheduled is not None and len(scheduled) == 2

    def test_owned_session_resolves_user_id_to_owner(self, monkeypatch):
        # An owned session resolves the Impression user_id to the session OWNER,
        # not merely the caller, and stamps the session_id + learn_path surface
        # (Req 2.6).
        clips = [make_clip(id="c0", topic_slug="t"), make_clip(id="c1", topic_slug="t")]
        _patch_path(monkeypatch, owner="owner", fetched_clips=clips)

        bg = RecordingBackgroundTasks()
        feeds = asyncio.run(get_path_feed("s1", bg, caller_id="owner"))

        assert [c.id for f in feeds for c in f.clips] == ["c0", "c1"]
        scheduled = _scheduled_impressions(bg)
        assert scheduled is not None and len(scheduled) == 2
        assert all(imp.user_id == "owner" for imp in scheduled)
        assert all(imp.session_id == "s1" for imp in scheduled)
        assert all(imp.feed_surface == "learn_path" for imp in scheduled)
        # Feed_Position is consecutive from 0 in serve order.
        assert [imp.feed_position for imp in scheduled] == [0, 1]

    def test_other_users_session_denied_records_nothing(self, monkeypatch):
        # A serve referencing a session owned by another user is rejected (403)
        # by the existing ownership check and schedules no Impression (Req 2.4).
        _patch_path(monkeypatch, owner="owner", fetched_clips=[make_clip(id="c0", topic_slug="t")])

        bg = RecordingBackgroundTasks()
        with pytest.raises(HTTPException) as exc:
            asyncio.run(get_path_feed("s1", bg, caller_id="attacker"))

        assert exc.value.status_code == 403
        assert _scheduled_impressions(bg) is None

    def test_nonexistent_session_records_nothing(self, monkeypatch):
        # A serve referencing a session that does not exist returns an empty feed
        # and schedules no Impression (Req 2.5).
        _patch_path(monkeypatch, owner="owner", fetched_clips=[make_clip(id="c0", topic_slug="t")])

        bg = RecordingBackgroundTasks()
        feeds = asyncio.run(get_path_feed("does-not-exist", bg, caller_id="owner"))

        assert feeds == []
        assert _scheduled_impressions(bg) is None
