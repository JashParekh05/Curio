"""Tests for the interest-delta logic (_event_delta), including the fix that a
skipped clip can never produce a positive interest signal."""
from hypothesis import given, strategies as st

from app.services.personalization import _event_delta, _get_session_telemetry, _update_interest_vector
from tests.conftest import FakeDB


class TestFeedbackPrecedence:
    def test_want_more_is_strong_positive(self):
        assert _event_delta(completed=False, replay_count=0, feedback="want_more") == 0.6

    def test_already_know_is_strong_negative_even_if_completed(self):
        assert _event_delta(completed=True, replay_count=5, feedback="already_know") == -1.0

    def test_feedback_overrides_completion(self):
        assert _event_delta(completed=True, replay_count=0, feedback="want_more") == 0.6


class TestCompleted:
    def test_completed_baseline(self):
        assert _event_delta(completed=True, replay_count=0) == 0.15

    def test_completed_replays_add(self):
        assert _event_delta(completed=True, replay_count=2) == 0.75  # 0.15 + 0.6


class TestSkipPenalty:
    def test_instant_bail(self):
        # watch_ratio < 0.1 of a 60s clip
        assert _event_delta(completed=False, replay_count=0, watch_ms=1_000, duration_seconds=60) == -0.30

    def test_casual_skip(self):
        # ~0.25 watched
        assert _event_delta(completed=False, replay_count=0, watch_ms=15_000, duration_seconds=60) == -0.10

    def test_mostly_watched_skip(self):
        # ~0.7 watched but not completed
        assert _event_delta(completed=False, replay_count=0, watch_ms=42_000, duration_seconds=60) == -0.02

    def test_missing_duration_defaults_to_60(self):
        # 1s of an unknown-duration clip -> treated as instant bail
        assert _event_delta(completed=False, replay_count=0, watch_ms=1_000, duration_seconds=None) == -0.30


class TestSkipNeverPositive:
    """The fix: replays on a skip mitigate but never flip the sign."""

    def test_mostly_watched_replay_no_longer_positive(self):
        # Previously -0.02 + 0.3 = +0.28 (a skip scoring as interest!). Now capped.
        assert _event_delta(completed=False, replay_count=1, watch_ms=42_000, duration_seconds=60) == 0.0

    def test_instant_bail_with_replay_caps_at_zero(self):
        assert _event_delta(completed=False, replay_count=1, watch_ms=1_000, duration_seconds=60) == 0.0

    def test_many_replays_still_capped(self):
        assert _event_delta(completed=False, replay_count=10, watch_ms=15_000, duration_seconds=60) == 0.0

    @given(
        replay=st.integers(min_value=0, max_value=100),
        watch_ms=st.integers(min_value=0, max_value=10_000_000),
        dur=st.integers(min_value=1, max_value=7200),
    )
    def test_skip_delta_is_never_positive(self, replay, watch_ms, dur):
        d = _event_delta(completed=False, replay_count=replay, watch_ms=watch_ms, duration_seconds=dur)
        assert d <= 0.0

    @given(replay=st.integers(min_value=0, max_value=100))
    def test_completed_delta_is_always_positive(self, replay):
        assert _event_delta(completed=True, replay_count=replay) >= 0.15


class TestGetSessionTelemetry:
    def _db(self, fail=None):
        return FakeDB(
            store={
                "clip_events": [
                    {"clip_id": "a", "watch_ms": 60000, "completed": True, "session_id": "s"},
                    {"clip_id": "b", "watch_ms": 5000, "completed": False, "session_id": "s"},
                    {"clip_id": "c", "watch_ms": 60000, "completed": True, "session_id": "s"},
                    {"clip_id": "x", "watch_ms": 1, "completed": False, "session_id": "other"},
                ],
                "clips": [
                    {"id": "a", "topic_slug": "t1"},
                    {"id": "b", "topic_slug": "t1"},
                    {"id": "c", "topic_slug": "t2"},
                ],
            },
            fail=fail,
        )

    def test_seen_ids_and_completion_rates(self):
        seen, completion = _get_session_telemetry(self._db(), "s")
        assert seen == {"a", "b", "c"}              # only this session's clips
        assert completion["t1"] == 0.5              # a=True, b=False
        assert completion["t2"] == 1.0              # c=True

    def test_event_fetch_failure_returns_empty(self):
        seen, completion = _get_session_telemetry(self._db(fail={"clip_events"}), "s")
        assert seen == set() and completion == {}

    def test_no_events_returns_empty(self):
        seen, completion = _get_session_telemetry(FakeDB(store={"clip_events": []}), "s")
        assert seen == set() and completion == {}


class TestUpdateInterestVector:
    def _session_emb(self, taste=None):
        return [{"session_id": "s", "taste_vector": taste}] if taste is not None else []

    def test_positive_event_writes_all_signals(self):
        db = FakeDB(store={"session_embeddings": []})
        _update_interest_vector(
            db, "s", "topic-x", completed=True, replay_count=0,
            clip_embedding=[1.0, 0.0], user_id="u",
        )
        # session interest merged with the completed delta (0.15)
        sess = db.rpc_named("merge_session_interest")
        assert sess and sess[0]["p_delta"] == 0.15
        # user interest merged at half weight
        assert db.rpc_named("merge_user_interest")[0]["p_delta"] == 0.075
        # taste moved: session upsert + user taste rpc, seeded to the clip embedding
        assert any(t == "session_embeddings" for t, _ in db.rec["upserts"])
        assert db.rpc_named("merge_user_taste")[0]["p_new_taste"] == [1.0, 0.0]

    def test_negative_event_does_not_touch_taste(self):
        db = FakeDB(store={"session_embeddings": []})
        _update_interest_vector(
            db, "s", "topic-x", completed=False, replay_count=0,
            watch_ms=1000, duration_seconds=60, clip_embedding=[1.0, 0.0], user_id="u",
        )
        assert db.rpc_named("merge_session_interest")[0]["p_delta"] == -0.30
        # taste never updated on a negative delta
        assert db.rec["upserts"] == []
        assert db.rpc_named("merge_user_taste") == []

    def test_sessionless_event_skips_session_writes_but_updates_user(self):
        db = FakeDB(store={"session_embeddings": []})
        _update_interest_vector(
            db, None, "topic-x", completed=True, replay_count=0,
            clip_embedding=[1.0, 0.0], user_id="u",
        )
        assert db.rpc_named("merge_session_interest") == []      # no session
        assert db.rec["upserts"] == []                            # no session taste upsert
        assert db.rpc_named("merge_user_interest")               # user still updated
        assert db.rpc_named("merge_user_taste")

    def test_want_more_feedback_uses_strong_positive_delta(self):
        db = FakeDB(store={"session_embeddings": []})
        _update_interest_vector(
            db, "s", "topic-x", completed=False, replay_count=0, feedback="want_more",
        )
        assert db.rpc_named("merge_session_interest")[0]["p_delta"] == 0.6