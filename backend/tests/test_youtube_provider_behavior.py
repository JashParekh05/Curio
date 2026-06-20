"""Integration tests for YouTube Content_Provider behavior preservation (Task 9.3).

These tests pin the structural guarantees that make ``YouTubeProvider`` a
behavior-preserving refactor of the existing YouTube path (Req 2). The provider
is a THIN adapter: ALL search/transcript work is delegated to the existing
``app.services.youtube`` charge site (``youtube_search`` / ``_fetch_transcript``),
and the provider itself introduces no new quota logic.

They run fully offline. The two external seams the provider reaches through
``youtube`` -- ``youtube.youtube_search`` and ``youtube._fetch_transcript`` -- are
replaced with doubles, and the Key_Pool charge primitive (``charge_and_persist``)
bound inside ``youtube`` is replaced with a spy so the tests can assert the
provider charges nothing of its own. ``YouTubeProvider.search`` /
``fetch_transcript`` import those names lazily (``from app.services.youtube import
...`` inside the method), so patching the attributes on the ``youtube`` module is
what the provider actually sees -- the same import-binding monkeypatch pattern
used by ``tests/test_youtube_search.py``.

What is verified:

  - ``search`` delegates to ``youtube.youtube_search`` and normalizes each
    returned video dict into a ``SourceItem`` whose ``provider_id`` and
    ``embed_ref.provider_id`` are both ``youtube`` and whose
    ``embed_ref.external_id`` is the video id (Req 2.2);
  - the persisted playback shape is preserved as
    ``youtube.com/embed/{id}?start=...`` (Req 2.5);
  - results are bounded to ``max_results`` in order (Req 1.1);
  - a cache-hit / canned return charges nothing: all spend routes through
    ``youtube.youtube_search``, the provider adds no separate charge (Req 2.4,
    2.7);
  - ``fetch_transcript`` delegates to ``youtube._fetch_transcript``, returns the
    transcript verbatim, and charges no Key_Pool quota (Req 2.3, 2.6, 2.7).

Validates: Requirements 2.2, 2.3, 2.4, 2.6, 2.7
"""
from __future__ import annotations

import pytest

from app.providers.youtube_provider import YouTubeProvider
from app.services import youtube
from app.services.content_provider import SourceItem

_QUERY = "binary search explained"

# A canned youtube_search result: the video-dict shape
# youtube._search_and_describe produces. Five videos so we can assert bounding.
_CANNED_VIDEOS = [
    {
        "video_id": f"vid{i}",
        "title": f"Lesson {i}",
        "description": f"A clear walkthrough number {i}.",
        "thumbnail_url": f"https://img/thumb{i}.jpg",
        "duration_seconds": 300 + i,
        "has_caption": True,
        "view_count": 1000 + i,
    }
    for i in range(5)
]

_CANNED_TRANSCRIPT = [
    {"start": 0.0, "duration": 2.5, "text": "Welcome to the lesson."},
    {"start": 2.5, "duration": 3.0, "text": "Let us begin."},
]


class _Spy:
    """Callable recording invocation count; stands in for a charge primitive."""

    def __init__(self, return_value=None):
        self.calls = 0
        self.return_value = return_value

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self.return_value


@pytest.fixture
def charge_spy(monkeypatch):
    """Replace the Key_Pool charge primitive bound in ``youtube`` with a spy.

    ``youtube`` binds ``charge_and_persist`` at import
    (``from app.services.quota_store import charge_and_persist``), so the bound
    copy lives in the ``youtube`` module -- patching it there is what any real
    charge would hit. The provider delegates search to the (separately mocked)
    ``youtube_search``, so a correctly behaving provider never reaches this spy
    for a search: all spend is encapsulated inside ``youtube_search``.
    """
    spy = _Spy(return_value=True)
    monkeypatch.setattr(youtube, "charge_and_persist", spy)
    return spy


# ---------------------------------------------------------------------------
# search(): delegation, normalization, identity, embed shape, bounding
# ---------------------------------------------------------------------------

class TestSearchDelegatesAndNormalizes:
    def test_returns_normalized_source_items_tagged_youtube(
        self, monkeypatch, charge_spy
    ):
        search_spy = _Spy(return_value=list(_CANNED_VIDEOS))
        monkeypatch.setattr(youtube, "youtube_search", search_spy)

        items = YouTubeProvider().search(_QUERY, max_results=5)

        # Delegated exactly once to the single charge site.
        assert search_spy.calls == 1
        assert len(items) == 5
        for item, video in zip(items, _CANNED_VIDEOS):
            assert isinstance(item, SourceItem)
            # provider_id and embed_ref.provider_id are both 'youtube' (Req 2.2).
            assert item.provider_id == "youtube"
            assert item.embed_ref.provider_id == "youtube"
            # external_id is the video id (Req 2.2).
            assert item.embed_ref.external_id == video["video_id"]
            assert item.title == video["title"]
            assert item.duration_seconds == video["duration_seconds"]

    def test_preserves_embed_playback_shape(self, monkeypatch, charge_spy):
        monkeypatch.setattr(
            youtube, "youtube_search", lambda q: [dict(_CANNED_VIDEOS[0])]
        )

        item = YouTubeProvider().search(_QUERY, max_results=1)[0]
        vid = item.embed_ref.external_id

        # The persisted playback URL keeps today's youtube.com/embed/{id}?start=...
        # shape (Req 2.5), reconstructable from external_id + playback_params.
        assert item.metadata["video_url"].startswith(
            f"https://www.youtube.com/embed/{vid}?start="
        )
        assert item.embed_ref.playback_params["start"] == "0"
        assert item.metadata["source_platform"] == "youtube"

    def test_bounds_results_to_max_results_in_order(self, monkeypatch, charge_spy):
        monkeypatch.setattr(youtube, "youtube_search", lambda q: list(_CANNED_VIDEOS))

        items = YouTubeProvider().search(_QUERY, max_results=2)

        # Bounded to N, order-preserving prefix (Req 1.1).
        assert len(items) == 2
        assert [i.embed_ref.external_id for i in items] == ["vid0", "vid1"]

    def test_empty_or_none_delegate_result_yields_no_items(
        self, monkeypatch, charge_spy
    ):
        monkeypatch.setattr(youtube, "youtube_search", lambda q: None)
        assert YouTubeProvider().search(_QUERY, max_results=5) == []

        monkeypatch.setattr(youtube, "youtube_search", lambda q: [])
        assert YouTubeProvider().search(_QUERY, max_results=5) == []


# ---------------------------------------------------------------------------
# search(): all spend routes through youtube_search; provider adds no charge
# ---------------------------------------------------------------------------

class TestSearchAddsNoSeparateCharge:
    def test_cache_hit_style_return_charges_nothing(self, monkeypatch, charge_spy):
        # Mocking youtube_search models the cache-first charge site returning a
        # canned/cached list. A correct provider neither charges nor reaches the
        # Key_Pool itself -- all spend is encapsulated inside youtube_search.
        search_spy = _Spy(return_value=list(_CANNED_VIDEOS))
        monkeypatch.setattr(youtube, "youtube_search", search_spy)

        items = YouTubeProvider().search(_QUERY, max_results=5)

        assert len(items) == 5
        # The provider adds no separate charge for search (Req 2.4, 2.7): every
        # billable decision lives behind the single youtube_search seam.
        assert charge_spy.calls == 0
        assert search_spy.calls == 1


# ---------------------------------------------------------------------------
# fetch_transcript(): delegation, verbatim return, no Key_Pool charge
# ---------------------------------------------------------------------------

class TestFetchTranscriptDelegates:
    def test_delegates_and_returns_transcript_without_charging(
        self, monkeypatch, charge_spy
    ):
        fetch_spy = _Spy(return_value=list(_CANNED_TRANSCRIPT))
        monkeypatch.setattr(youtube, "_fetch_transcript", fetch_spy)

        result = YouTubeProvider().fetch_transcript("vid0")

        # Delegates to youtube._fetch_transcript and returns its result verbatim
        # (Req 2.3, 2.6).
        assert fetch_spy.calls == 1
        assert result == _CANNED_TRANSCRIPT
        # Transcripts are not a YouTube-quota operation: no Key_Pool charge (Req 2.7).
        assert charge_spy.calls == 0

    def test_blank_external_id_short_circuits_without_delegating(
        self, monkeypatch, charge_spy
    ):
        fetch_spy = _Spy(return_value=list(_CANNED_TRANSCRIPT))
        monkeypatch.setattr(youtube, "_fetch_transcript", fetch_spy)

        assert YouTubeProvider().fetch_transcript("   ") is None
        # No delegation, no charge for an empty id.
        assert fetch_spy.calls == 0
        assert charge_spy.calls == 0
