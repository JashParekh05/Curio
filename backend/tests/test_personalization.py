"""Tests for the interest-delta logic (_event_delta), including the fix that a
skipped clip can never produce a positive interest signal."""
from hypothesis import given, strategies as st

from app.services.personalization import _event_delta


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
