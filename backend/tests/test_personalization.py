from app.services.personalization import compute_interest_delta


def _delta(**kw):
    base = dict(
        watch_ms=0,
        duration_seconds=100,
        completed=False,
        replay_count=0,
        feedback=None,
        baseline_watch_ratio=None,
    )
    base.update(kw)
    return compute_interest_delta(**base)


class TestFeedbackOverrides:
    def test_want_more(self):
        assert _delta(feedback="want_more") == 0.6

    def test_already_know(self):
        assert _delta(feedback="already_know") == -1.0

    def test_feedback_ignores_rewatch(self):
        # Explicit feedback is a complete signal; replays don't modify it.
        assert _delta(feedback="want_more", replay_count=5) == 0.6


class TestCompletion:
    def test_completion_is_positive_regardless_of_baseline(self):
        assert _delta(completed=True, baseline_watch_ratio=0.1) == 0.15
        assert _delta(completed=True, baseline_watch_ratio=0.9) == 0.15


class TestRelativeSkip:
    """The core insight: the SAME raw watch ratio means different things for
    different users, judged against their own baseline."""

    def test_same_watch_ratio_different_users(self):
        # watch_ratio = 35000 / 1000 / 100 = 0.35 for both.
        skimmer = _delta(watch_ms=35000, baseline_watch_ratio=0.35)      # rel ~1.0
        completionist = _delta(watch_ms=35000, baseline_watch_ratio=0.9)  # rel ~0.39
        assert skimmer == 0.05          # watched what they usually do → not a reject
        assert completionist == -0.15   # well below their norm → negative
        assert skimmer > completionist

    def test_bailing_unusually_fast_is_strong_negative(self):
        # watch_ratio = 0.05; rel = 0.05 / 0.5 = 0.1 → strongest penalty.
        assert _delta(watch_ms=5000, baseline_watch_ratio=0.5) == -0.30

    def test_cold_start_uses_default_baseline(self):
        # No baseline behaves exactly like baseline 0.5.
        assert _delta(watch_ms=35000, baseline_watch_ratio=None) == _delta(
            watch_ms=35000, baseline_watch_ratio=0.5
        )


class TestRewatchWeighting:
    def test_zero_at_no_replay(self):
        assert _delta(completed=True, replay_count=0) == 0.15  # no bonus

    def test_monotonic_with_diminishing_returns(self):
        d0 = _delta(completed=True, replay_count=0)
        d1 = _delta(completed=True, replay_count=1)
        d2 = _delta(completed=True, replay_count=2)
        assert d1 > d0 and d2 > d1               # rewatching adds positive signal
        assert (d1 - d0) > (d2 - d1)             # but with diminishing returns

    def test_bounded(self):
        d_big = _delta(completed=True, replay_count=100)
        assert d_big <= 0.50                      # base 0.15 + capped bonus 0.35
        assert d_big > _delta(completed=True, replay_count=2)

    def test_rewatch_flips_a_mild_skip_positive(self):
        # A mild skip (rel ~0.7 → -0.05) that the user replayed reads as interest.
        assert _delta(watch_ms=35000, baseline_watch_ratio=0.5, replay_count=0) < 0
        assert _delta(watch_ms=35000, baseline_watch_ratio=0.5, replay_count=1) > 0
