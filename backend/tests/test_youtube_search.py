"""Integration tests for the single YouTube quota charge site (Task 9.3).

Exercises ``app.services.youtube.youtube_search`` -- the one place that spends
YouTube Data API v3 quota -- with the HTTP layer (``requests``), the search
cache (``search_cache_get``/``search_cache_put``), and the quota store
(``load_today``/``charge_and_persist``/``configured_projects``) all replaced by
doubles patched in the ``youtube`` module namespace. ``youtube`` binds those
names at import (``from app.services.quota_store import ...``), so the bound
copies live in the ``youtube`` module and that is where they are patched (the
same import-binding pattern used by ``tests/test_quota_store.py``).

Verifies the three charge-site guarantees:

  - cache hit: a ``youtube_search_cache`` hit returns the cached videos, charges
    nothing, never selects a project, and issues no HTTP request (0 units);
  - real search (cache miss): the 100-unit search charge is persisted via
    ``charge_and_persist`` BEFORE the ``youtube/v3/search`` HTTP call
    (charge-before-call ordering), the 1-unit metadata charge precedes the
    ``videos.list`` call, and the result is cached;
  - exhausted pool: when no project can afford a search, ``youtube_search``
    returns None and spends nothing (no charge, no HTTP).

Validates: Requirements 2.5, 6.3, 6.4
"""
from datetime import datetime, timezone

import pytest

from app.services import youtube
from app.services.quota_pool import DAILY_QUOTA, METADATA_COST, ProjectQuota, SEARCH_COST

# A fixed instant; youtube_search only forwards it to the (mocked) quota store.
_NOW = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
_QUERY = "binary search explained"

# Canonical youtube/v3/search and videos.list response payloads.
_SEARCH_PAYLOAD = {
    "items": [
        {
            "id": {"videoId": "vid1"},
            "snippet": {
                "title": "Binary Search",
                "description": "A clear walkthrough of binary search.",
                "thumbnails": {"high": {"url": "https://img/thumb1.jpg"}},
            },
        }
    ]
}
_VIDEOS_PAYLOAD = {
    "items": [
        {
            "id": "vid1",
            "contentDetails": {"duration": "PT5M30S", "caption": "true"},
            "snippet": {},
            "statistics": {"viewCount": "4200"},
        }
    ]
}


class _FakeResponse:
    """Minimal ``requests``-style response: ``.ok``, ``.status_code``, ``.json()``."""

    def __init__(self, payload, ok=True, status_code=200):
        self._payload = payload
        self.ok = ok
        self.status_code = status_code

    def json(self):
        return self._payload


class _FakeRequests:
    """Stand-in for the ``requests`` module bound inside ``youtube``.

    Records an ordered event for every call so tests can assert the
    charge-before-call ordering, and dispatches on the URL to return the search
    vs videos.list payload. Exposes ``RequestException`` because the charge site
    references ``requests.RequestException`` in its except clauses.
    """

    RequestException = Exception

    def __init__(self, events):
        self.events = events

    def get(self, url, params=None, timeout=None):
        self.events.append(("http", url))
        if url == youtube._SEARCH_URL:
            return _FakeResponse(_SEARCH_PAYLOAD)
        return _FakeResponse(_VIDEOS_PAYLOAD)


def _install_charge(events, *, ok=True):
    """Return a charge_and_persist double that records ('charge', project, cost)."""

    def _charge(project_id, cost, now_utc=None):
        events.append(("charge", project_id, cost))
        return ok

    return _charge


class _Spy:
    """Callable that records how many times it was invoked and returns a value."""

    def __init__(self, return_value=None):
        self.calls = 0
        self.return_value = return_value

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self.return_value


# --------------------------------------------------------------------------
# Cache hit: zero units, pool untouched, no HTTP
# --------------------------------------------------------------------------

class TestCacheHit:
    def test_cache_hit_returns_cached_videos_and_spends_nothing(self, monkeypatch):
        cached = [{"video_id": "cachedA", "title": "Cached"}]
        events: list = []

        # Cache hit short-circuits everything downstream.
        monkeypatch.setattr(youtube, "search_cache_get", lambda q: cached)

        # Spies that must never be called on a cache hit.
        configured = _Spy(return_value=[("projA", "keyA")])
        load_today = _Spy(return_value=[ProjectQuota("projA", 0)])
        cache_put = _Spy()
        monkeypatch.setattr(youtube, "configured_projects", configured)
        monkeypatch.setattr(youtube, "load_today", load_today)
        monkeypatch.setattr(youtube, "search_cache_put", cache_put)
        monkeypatch.setattr(youtube, "charge_and_persist", _install_charge(events))
        monkeypatch.setattr(youtube, "requests", _FakeRequests(events))

        result = youtube.youtube_search(_QUERY, now_utc=_NOW)

        # Returns exactly the cached videos.
        assert result == cached
        # Never touched the pool, never charged, never hit the network.
        assert configured.calls == 0
        assert load_today.calls == 0
        assert cache_put.calls == 0
        assert events == []


# --------------------------------------------------------------------------
# Cache miss (real search): charge 100 BEFORE the HTTP search call
# --------------------------------------------------------------------------

class TestRealSearchChargesBeforeCall:
    @pytest.fixture
    def one_project(self, monkeypatch):
        monkeypatch.setattr(youtube, "search_cache_get", lambda q: None)
        monkeypatch.setattr(youtube, "configured_projects", lambda: [("projA", "keyA")])
        monkeypatch.setattr(youtube, "load_today", lambda now=None: [ProjectQuota("projA", 0)])

    def test_persists_search_charge_before_http_call(self, monkeypatch, one_project):
        events: list = []
        monkeypatch.setattr(youtube, "charge_and_persist", _install_charge(events))
        monkeypatch.setattr(youtube, "requests", _FakeRequests(events))
        monkeypatch.setattr(youtube, "search_cache_put", lambda q, v: None)

        youtube.youtube_search(_QUERY, now_utc=_NOW)

        # The first recorded event is the 100-unit search charge, and it lands
        # strictly before the youtube/v3/search HTTP request (charge-before-call).
        assert events[0] == ("charge", "projA", SEARCH_COST)
        assert events[1] == ("http", youtube._SEARCH_URL)

    def test_full_ordering_search_then_metadata_then_videos_list(
        self, monkeypatch, one_project
    ):
        events: list = []
        monkeypatch.setattr(youtube, "charge_and_persist", _install_charge(events))
        monkeypatch.setattr(youtube, "requests", _FakeRequests(events))
        monkeypatch.setattr(youtube, "search_cache_put", lambda q, v: None)

        youtube.youtube_search(_QUERY, now_utc=_NOW)

        # Each charge precedes the HTTP call it pays for: 100u then search,
        # 1u (metadata) then videos.list.
        assert events == [
            ("charge", "projA", SEARCH_COST),
            ("http", youtube._SEARCH_URL),
            ("charge", "projA", METADATA_COST),
            ("http", youtube._VIDEOS_URL),
        ]

    def test_returns_parsed_videos_and_caches_result(self, monkeypatch, one_project):
        events: list = []
        cached_payloads: list = []
        monkeypatch.setattr(youtube, "charge_and_persist", _install_charge(events))
        monkeypatch.setattr(youtube, "requests", _FakeRequests(events))
        monkeypatch.setattr(
            youtube,
            "search_cache_put",
            lambda q, v: cached_payloads.append((q, v)),
        )

        result = youtube.youtube_search(_QUERY, now_utc=_NOW)

        assert result is not None and len(result) == 1
        video = result[0]
        assert video["video_id"] == "vid1"
        assert video["title"] == "Binary Search"
        assert video["duration_seconds"] == 5 * 60 + 30  # PT5M30S
        assert video["has_caption"] is True
        assert video["view_count"] == 4200
        # The successful result is written back to the search cache verbatim.
        assert cached_payloads == [(_QUERY, result)]

    def test_metadata_charge_failure_skips_videos_list_but_still_returns(
        self, monkeypatch, one_project
    ):
        # The search charge (100) succeeds; the metadata charge (1) fails closed,
        # so videos.list is skipped -- but the search results remain usable.
        events: list = []

        def _charge(project_id, cost, now_utc=None):
            events.append(("charge", project_id, cost))
            return cost == SEARCH_COST  # only the search charge persists

        monkeypatch.setattr(youtube, "charge_and_persist", _charge)
        monkeypatch.setattr(youtube, "requests", _FakeRequests(events))
        monkeypatch.setattr(youtube, "search_cache_put", lambda q, v: None)

        result = youtube.youtube_search(_QUERY, now_utc=_NOW)

        # videos.list was never issued (no second HTTP event).
        assert ("http", youtube._VIDEOS_URL) not in events
        assert events == [
            ("charge", "projA", SEARCH_COST),
            ("http", youtube._SEARCH_URL),
            ("charge", "projA", METADATA_COST),
        ]
        # Search results still returned, falling back to default metadata.
        assert result is not None and result[0]["video_id"] == "vid1"
        assert result[0]["duration_seconds"] == 180  # default when no videos.list


# --------------------------------------------------------------------------
# Exhausted pool: return None, spend nothing
# --------------------------------------------------------------------------

class TestExhaustedPool:
    def test_no_affordable_project_returns_none_without_spending(self, monkeypatch):
        events: list = []
        monkeypatch.setattr(youtube, "search_cache_get", lambda q: None)
        monkeypatch.setattr(youtube, "configured_projects", lambda: [("projA", "keyA")])
        # The only project is fully used => nothing can afford a 100-unit search.
        monkeypatch.setattr(
            youtube, "load_today", lambda now=None: [ProjectQuota("projA", DAILY_QUOTA)]
        )
        monkeypatch.setattr(youtube, "charge_and_persist", _install_charge(events))
        monkeypatch.setattr(youtube, "requests", _FakeRequests(events))
        cache_put = _Spy()
        monkeypatch.setattr(youtube, "search_cache_put", cache_put)

        result = youtube.youtube_search(_QUERY, now_utc=_NOW)

        assert result is None
        # No charge, no HTTP, nothing cached.
        assert events == []
        assert cache_put.calls == 0

    def test_no_configured_projects_returns_none_without_spending(self, monkeypatch):
        events: list = []
        monkeypatch.setattr(youtube, "search_cache_get", lambda q: None)
        monkeypatch.setattr(youtube, "configured_projects", lambda: [])
        monkeypatch.setattr(youtube, "charge_and_persist", _install_charge(events))
        monkeypatch.setattr(youtube, "requests", _FakeRequests(events))

        result = youtube.youtube_search(_QUERY, now_utc=_NOW)

        assert result is None
        assert events == []
