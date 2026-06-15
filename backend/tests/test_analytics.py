from app.services.analytics import compute_dropoff, _watch_ratio, topic_dropoff
from tests.conftest import FakeDB


def _meta(**by_id):
    return by_id


class TestWatchRatio:
    def test_basic(self):
        assert _watch_ratio(30_000, 60) == 0.5

    def test_caps_at_one(self):
        assert _watch_ratio(120_000, 60) == 1.0

    def test_zero_or_missing_duration(self):
        assert _watch_ratio(1000, 0) is None
        assert _watch_ratio(1000, None) is None

    def test_missing_watch(self):
        assert _watch_ratio(None, 60) is None

    def test_negative_clamped(self):
        assert _watch_ratio(-5000, 60) == 0.0


class TestComputeDropoff:
    def test_empty_events(self):
        assert compute_dropoff({"c": {"section_index": 0, "duration_seconds": 60}}, []) == []

    def test_funnel_orders_by_section_and_computes_retention(self):
        meta = {
            "a": {"section_index": 0, "duration_seconds": 60},
            "b": {"section_index": 1, "duration_seconds": 60},
            "c": {"section_index": 2, "duration_seconds": 60},
        }
        # 4 views beat 0, 2 views beat 1, 1 view beat 2 — classic decay.
        events = (
            [{"clip_id": "a", "watch_ms": 60_000, "completed": True}] * 4
            + [{"clip_id": "b", "watch_ms": 30_000, "completed": False}] * 2
            + [{"clip_id": "c", "watch_ms": 60_000, "completed": True}] * 1
        )
        out = compute_dropoff(meta, events)
        assert [r["section_index"] for r in out] == [0, 1, 2]
        assert [r["views"] for r in out] == [4, 2, 1]
        assert out[0]["retention_vs_first"] == 1.0
        assert out[1]["retention_vs_first"] == 0.5
        assert out[2]["retention_vs_first"] == 0.25
        assert out[0]["completion_rate"] == 1.0
        assert out[1]["completion_rate"] == 0.0
        assert out[1]["avg_watch_ratio"] == 0.5

    def test_ignores_events_without_metadata(self):
        meta = {"a": {"section_index": 0, "duration_seconds": 60}}
        events = [
            {"clip_id": "a", "watch_ms": 60_000, "completed": True},
            {"clip_id": "ghost", "watch_ms": 1000, "completed": False},  # no meta
        ]
        out = compute_dropoff(meta, events)
        assert len(out) == 1
        assert out[0]["views"] == 1

    def test_none_section_sorts_last(self):
        meta = {
            "a": {"section_index": 0, "duration_seconds": 60},
            "x": {"section_index": None, "duration_seconds": 60},
        }
        events = [
            {"clip_id": "a", "watch_ms": 60_000, "completed": True},
            {"clip_id": "x", "watch_ms": 60_000, "completed": True},
        ]
        out = compute_dropoff(meta, events)
        assert [r["section_index"] for r in out] == [0, None]

    def test_avg_watch_ratio_none_when_no_durations(self):
        meta = {"a": {"section_index": 0, "duration_seconds": None}}
        events = [{"clip_id": "a", "watch_ms": 30_000, "completed": False}]
        out = compute_dropoff(meta, events)
        assert out[0]["avg_watch_ratio"] is None
        assert out[0]["views"] == 1


class TestTopicDropoffWrapper:
    def _db(self, fail=None):
        return FakeDB(
            store={
                "clips": [
                    {"id": "a", "topic_slug": "t", "section_index": 0, "duration_seconds": 60},
                    {"id": "b", "topic_slug": "t", "section_index": 1, "duration_seconds": 60},
                    {"id": "z", "topic_slug": "other", "section_index": 0, "duration_seconds": 60},
                ],
                "clip_events": [
                    {"clip_id": "a", "watch_ms": 60000, "completed": True},
                    {"clip_id": "a", "watch_ms": 60000, "completed": True},
                    {"clip_id": "b", "watch_ms": 30000, "completed": False},
                ],
            },
            fail=fail,
        )

    def test_builds_funnel_for_topic_only(self):
        funnel = topic_dropoff_with(self._db(), "t")
        assert [r["section_index"] for r in funnel] == [0, 1]
        assert funnel[0]["views"] == 2 and funnel[1]["views"] == 1
        assert funnel[1]["retention_vs_first"] == 0.5

    def test_no_clips_returns_empty(self):
        assert topic_dropoff_with(FakeDB(store={"clips": []}), "t") == []

    def test_clips_fetch_failure_returns_empty(self):
        assert topic_dropoff_with(self._db(fail={"clips"}), "t") == []

    def test_events_fetch_failure_returns_empty(self):
        assert topic_dropoff_with(self._db(fail={"clip_events"}), "t") == []


def topic_dropoff_with(db, slug):
    """topic_dropoff resolves its own client via get_client; patch it for the
    duration of the call so the fake DB is used."""
    import app.db.supabase as supa
    orig = supa.get_client
    supa.get_client = lambda: db
    try:
        return topic_dropoff(slug)
    finally:
        supa.get_client = orig
