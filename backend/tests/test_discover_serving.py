"""Integration tests for the cold-start Discover serving path
(`get_discover_feed` in app/api/feed.py).

These exercise the real serving logic — level-aware ranking
(`exclude_below` + `rank_by_level`), the empty-library `processing = true`
envelope, and the non-blocking background `Per_User_Topup` scheduling — driving
the handler directly (no HTTP) against a faked Supabase client, mirroring the
fake-DB pattern used by tests/test_feed_retrieval.py and tests/test_quiz_api.py.

Clip retrieval itself is covered by test_feed_retrieval.py; here we stub
`_fetch_discover_clips` so we can construct clips with known `content_level`
values and assert the serving path's level ordering / exclusion + envelope.

Validates: Requirements 5.1, 5.2, 5.4, 5.6, 6.2
"""
import asyncio

import app.api.feed as feed_api
from app.api.feed import get_discover_feed
from app.models.schemas import DiscoverResponse
from tests.conftest import FakeDB, make_clip


class RecordingBackgroundTasks:
    """Stand-in for fastapi.BackgroundTasks that ONLY records scheduled tasks.

    It never runs them, so a returned response proves the handler did not await
    (or otherwise run) the background top-up on the request path."""

    def __init__(self):
        self.tasks: list[tuple] = []

    def add_task(self, func, *args, **kwargs):
        self.tasks.append((func, args, kwargs))


def _profile(user_id="u", interests=None, grade_level="high_school"):
    return {
        "user_id": user_id,
        "interests": interests if interests is not None else ["math"],
        # Non-None taste_vector so the handler skips the cold-start embed path.
        "taste_vector": [0.1, 0.2],
        "interest_vector": {},
        "grade_level": grade_level,
    }


def _patch(monkeypatch, *, profile, fetched_clips):
    """Wire a fake DB + stubbed retrieval/matching so the serving path runs in
    isolation. Returns the FakeDB for assertions."""
    db = FakeDB(store={
        "user_profiles": [profile],
        "learning_paths": [],          # no sessions -> empty seen_ids
        "topics": [{"slug": "t"}, {"slug": "other"}],
        "clips": [],                   # empty library -> fallback adds nothing
    })
    monkeypatch.setattr(feed_api, "get_client", lambda: db)
    # Avoid embeddings in interest matching; retrieval is tested elsewhere.
    monkeypatch.setattr(feed_api, "_match_interest_slugs", lambda *a, **k: ["t"])
    monkeypatch.setattr(feed_api, "_fetch_discover_clips", lambda *a, **k: list(fetched_clips))
    return db


class TestLevelAwareServing:
    def test_level_match_first_ordering_and_below_level_exclusion(self, monkeypatch):
        # high_school -> intermediate (Content_Level). Build a mix of levels.
        adv = make_clip(id="adv", topic_slug="t", content_level="advanced")
        int1 = make_clip(id="int1", topic_slug="t", content_level="intermediate")
        beg = make_clip(id="beg", topic_slug="t", content_level="beginner")
        int2 = make_clip(id="int2", topic_slug="t", content_level="intermediate")
        nolevel = make_clip(id="nolevel", topic_slug="t", content_level=None)

        _patch(monkeypatch, profile=_profile(grade_level="high_school"),
               fetched_clips=[adv, int1, beg, int2, nolevel])

        bg = RecordingBackgroundTasks()
        resp = asyncio.run(get_discover_feed("u", bg, limit=20, caller_id="u"))

        assert isinstance(resp, DiscoverResponse)
        served = [c.id for c in resp.clips]

        # Below-level exclusion: a beginner clip is dropped while an exact
        # (intermediate) match exists (Req 4.5).
        assert "beg" not in served

        # Level-match-first: every intermediate clip precedes every
        # non-matching clip, and that ordering dominates (Req 4.3, 4.4).
        levels = [c.content_level for c in resp.clips]
        last_match = max(i for i, lv in enumerate(levels) if lv == "intermediate")
        first_nonmatch = min(i for i, lv in enumerate(levels) if lv != "intermediate")
        assert last_match < first_nonmatch
        # Exact level group leads in its incoming (personalized) order.
        assert served[:2] == ["int1", "int2"]
        assert resp.processing is False

    def test_no_match_keeps_clips_via_soft_fallback(self, monkeypatch):
        # No intermediate clip -> exclude_below must NOT drop everything; the
        # feed is still served (soft fallback ordering, Req 4.6/5.5).
        adv = make_clip(id="adv", topic_slug="t", content_level="advanced")
        beg = make_clip(id="beg", topic_slug="t", content_level="beginner")

        _patch(monkeypatch, profile=_profile(grade_level="high_school"),
               fetched_clips=[adv, beg])

        bg = RecordingBackgroundTasks()
        resp = asyncio.run(get_discover_feed("u", bg, limit=20, caller_id="u"))

        served = {c.id for c in resp.clips}
        assert served == {"adv", "beg"}
        assert resp.processing is False


class TestEmptyLibraryEnvelope:
    def test_empty_library_returns_processing_true(self, monkeypatch):
        _patch(monkeypatch, profile=_profile(), fetched_clips=[])

        bg = RecordingBackgroundTasks()
        resp = asyncio.run(get_discover_feed("u", bg, limit=20, caller_id="u"))

        assert isinstance(resp, DiscoverResponse)
        assert resp.clips == []
        assert resp.processing is True   # library empty + topup running (Req 5.6)


class TestBackgroundTopupNonBlocking:
    def test_topup_scheduled_and_not_awaited(self, monkeypatch):
        # Sentinel top-up: flips a flag if it is ever actually executed.
        ran = {"executed": False}

        def fake_seed_topics_bg(*args, **kwargs):
            ran["executed"] = True

        monkeypatch.setattr(feed_api, "_seed_topics_bg", fake_seed_topics_bg)

        _patch(monkeypatch, profile=_profile(interests=["math"], grade_level="high_school"),
               fetched_clips=[make_clip(id="int1", topic_slug="t", content_level="intermediate")])

        bg = RecordingBackgroundTasks()
        resp = asyncio.run(get_discover_feed("u", bg, limit=20, caller_id="u"))

        # Response is produced without the top-up having run (Req 5.4, 6.2).
        assert isinstance(resp, DiscoverResponse)
        assert ran["executed"] is False

        # The Per_User_Topup IS scheduled as a background task (Req 5.1, 5.2).
        scheduled = [t for t in bg.tasks if t[0] is fake_seed_topics_bg]
        assert len(scheduled) == 1
        # It was handed grade-aligned seed slugs to generate, not awaited.
        seed_slugs = scheduled[0][1][0]
        assert isinstance(seed_slugs, list) and seed_slugs

    def test_topup_not_scheduled_without_interests(self, monkeypatch):
        # No interests -> no seed slugs -> no top-up task scheduled.
        monkeypatch.setattr(feed_api, "_seed_topics_bg", lambda *a, **k: None)
        _patch(monkeypatch, profile=_profile(interests=[]), fetched_clips=[])

        bg = RecordingBackgroundTasks()
        asyncio.run(get_discover_feed("u", bg, limit=20, caller_id="u"))

        assert bg.tasks == []
