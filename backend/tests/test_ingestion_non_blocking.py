"""Integration test for non-blocking ingestion (Task 11.4).

Req 9.1: multi-provider Source_Selection + ingestion run OUTSIDE the
request/response path, so no Discover or feed request waits on ingestion
completion and none fails or is delayed as a result of ingestion.

The Discover endpoint (``app.api.feed.get_discover_feed``) is the learner-facing
entrypoint that triggers ingestion. Its only ingestion hook is scheduling
``_seed_topics_bg`` as a FastAPI BackgroundTask; the ingestion chain that
acquisition + the shared pipeline run on is

    _seed_topics_bg -> run_pipeline -> ingest_topic -> source_acquirer.acquire_sources

We drive the handler directly (no HTTP) against a faked Supabase client, mirroring
``tests/test_discover_serving.py``, and use a ``RecordingBackgroundTasks`` that
records but NEVER runs scheduled tasks -- so a returned response proves the handler
did not run (let alone await) ingestion on the request path.

Two guarantees are asserted:

  1. **Not blocked / not on the request path.** The Discover request returns a
     ``DiscoverResponse`` WITHOUT the ingestion seams (``acquire_sources`` /
     ``ingest_topic``) ever being invoked on the request path, while the ingestion
     work IS scheduled as a background task (so it runs out-of-band). The seams are
     wired to BOMB if reached inline, so reaching them would fail the test.

  2. **An ingestion failure does not fail a learner-facing request.** Even with the
     ingestion seams raising, the request still returns successfully (it never
     touches them on the request path), and the background ingestion entrypoint
     (``_seed_topics_bg``) itself swallows the failure and never raises -- so a
     broken provider / pipeline can never surface as a failed or delayed Discover
     request.

Offline: DB / model / youtube seams are faked exactly as the existing tests do; no
external service is touched.

Run from the backend/ dir:
``.venv/bin/python -m pytest tests/test_ingestion_non_blocking.py``.

Validates: Requirements 9.1
"""
import asyncio

import app.api.feed as feed_api
import app.services.discover_seeding as discover_seeding
import app.services.ingestion_pipeline as ip
import app.services.source_acquirer as sa
from app.api.feed import get_discover_feed
from app.models.schemas import DiscoverResponse
from tests.conftest import FakeDB, make_clip


class RecordingBackgroundTasks:
    """Stand-in for fastapi.BackgroundTasks that ONLY records scheduled tasks.

    It never runs them, so a returned response proves the handler did not await
    (or otherwise run) ingestion on the request path."""

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


def _patch_serving(monkeypatch, *, profile, fetched_clips):
    """Wire a fake DB + stubbed retrieval/matching so the Discover serving path
    runs in isolation, mirroring tests/test_discover_serving.py. Returns the
    FakeDB for assertions."""
    db = FakeDB(store={
        "user_profiles": [profile],
        "learning_paths": [],          # no sessions -> empty seen_ids
        "topics": [{"slug": "t"}],
        "clips": [],                   # empty library -> fallback adds nothing
    })
    monkeypatch.setattr(feed_api, "get_client", lambda: db)
    # Avoid embeddings in interest matching; retrieval is tested elsewhere.
    monkeypatch.setattr(feed_api, "_match_interest_slugs", lambda *a, **k: ["t"])
    monkeypatch.setattr(feed_api, "_fetch_discover_clips", lambda *a, **k: list(fetched_clips))
    return db


def _arm_ingestion_bombs(monkeypatch):
    """Wire the ingestion seams so that reaching EITHER of them records the hit
    and raises. Patching the module attributes intercepts the lazily-imported
    names: run_pipeline does ``from app.services.ingestion_pipeline import
    ingest_topic`` and ingest_topic does ``... import acquire_sources`` at call
    time, so these patches catch any inline ingestion on the request path.

    Returns a dict whose flags stay False unless a seam is invoked."""
    hit = {"ingest_topic": False, "acquire_sources": False}

    def _bomb_ingest(*args, **kwargs):
        hit["ingest_topic"] = True
        raise AssertionError("ingest_topic must not run on the request path")

    def _bomb_acquire(*args, **kwargs):
        hit["acquire_sources"] = True
        raise AssertionError("acquire_sources must not run on the request path")

    monkeypatch.setattr(ip, "ingest_topic", _bomb_ingest)
    monkeypatch.setattr(sa, "acquire_sources", _bomb_acquire)
    return hit


# --------------------------------------------------------------------------
# 1. The Discover request returns without ingestion on the request path
# --------------------------------------------------------------------------


class TestDiscoverDoesNotBlockOnIngestion:
    def test_response_returns_without_invoking_ingestion_seams(self, monkeypatch):
        """The Discover response is produced WITHOUT acquisition + ingestion ever
        running inline, while the ingestion work is scheduled as a background task
        (Req 9.1)."""
        hit = _arm_ingestion_bombs(monkeypatch)
        _patch_serving(
            monkeypatch,
            profile=_profile(interests=["math"], grade_level="high_school"),
            fetched_clips=[make_clip(id="c1", topic_slug="t", content_level="intermediate")],
        )

        bg = RecordingBackgroundTasks()
        resp = asyncio.run(get_discover_feed("u", bg, limit=20, caller_id="u"))

        # The learner-facing request returned a normal response.
        assert isinstance(resp, DiscoverResponse)

        # Neither ingestion seam was touched on the request path: acquisition and
        # the shared pipeline never ran inline, so the request did not wait on them.
        assert hit == {"ingest_topic": False, "acquire_sources": False}

        # Ingestion IS deferred to a background task (runs out-of-band), proving
        # the work is performed outside the request/response path (Req 9.1).
        scheduled = [t for t in bg.tasks if t[0] is feed_api._seed_topics_bg]
        assert len(scheduled) == 1
        seed_slugs = scheduled[0][1][0]
        assert isinstance(seed_slugs, list) and seed_slugs

    def test_response_returns_even_when_ingestion_seams_would_raise(self, monkeypatch):
        """An ingestion failure cannot fail the learner-facing request: with the
        ingestion seams wired to raise, the Discover request still returns a normal
        response because ingestion never runs on the request path (Req 9.1)."""
        _arm_ingestion_bombs(monkeypatch)
        _patch_serving(
            monkeypatch,
            profile=_profile(interests=["math"]),
            fetched_clips=[],   # empty library -> processing envelope, still a success
        )

        bg = RecordingBackgroundTasks()
        resp = asyncio.run(get_discover_feed("u", bg, limit=20, caller_id="u"))

        # The request succeeds despite the (background) ingestion path being broken.
        assert isinstance(resp, DiscoverResponse)
        assert resp.processing is True


# --------------------------------------------------------------------------
# 2. A failing background ingestion never escapes to fail the request
# --------------------------------------------------------------------------


class TestBackgroundIngestionFailureIsContained:
    def test_seed_topics_bg_swallows_ingestion_failure(self, monkeypatch):
        """The scheduled background entrypoint drives the real
        ``run_pipeline -> ingest_topic`` chain; when ingestion raises, the
        background task swallows it and never raises, so a learner-facing request
        that scheduled it can never be failed or delayed by ingestion (Req 9.1)."""
        # ingest_topic is the chain seam: run_pipeline (whole-topic) calls it via a
        # lazy import, so patching the module attribute makes the background
        # ingestion blow up the way a broken provider/pipeline would.
        def _raise_ingest(*args, **kwargs):
            raise RuntimeError("ingestion backend exploded")

        monkeypatch.setattr(ip, "ingest_topic", _raise_ingest)

        # Empty topics/clips -> the seeder inserts the topic then invokes the
        # pipeline (which fails); no external service is touched.
        db = FakeDB(store={"topics": [], "clips": []})
        monkeypatch.setattr(discover_seeding, "get_client", lambda: db)

        # Must not raise -- the background task contains the failure entirely.
        discover_seeding._seed_topics_bg(["t"], "intermediate")
