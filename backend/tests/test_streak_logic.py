"""Unit tests for Streak and Freeze_Token logic (Phase 2, Req 15).

These exercise the pure, deterministic streak helpers in
``app.services.game``: ``is_qualifying_activity``, ``update_streak``, and the
``register_activity`` convenience wrapper around ``StreakState``. Every
transition takes the calendar ``day`` explicitly as a ``datetime.date``, so the
assertions are wall-clock independent and fully deterministic.

Coverage:

- **Advance/defend counts a day (Req 15.1).** A qualifying ADVANCE or DEFEND day
  counts toward the Streak and increments the consecutive-day count; a repeat on
  the same day never double-counts.
- **Freeze_Token preserves an inactive day (Req 15.2).** An inactive day with a
  Freeze_Token held preserves the Streak count and consumes exactly one token.
- **No token resets the Streak (Req 15.3).** An inactive day with no token
  remaining resets the Streak to zero.
- **Raw OPEN is treated as inactive (Req 15.4).** The Streak is based on
  advance-or-defend activity, so a raw app OPEN (and NONE) behaves exactly like
  an inactive day.

Validates: Requirements 15.1, 15.2, 15.3, 15.4
"""
from datetime import date

from app.services import game

# Explicit, consecutive calendar days for deterministic transitions.
DAY1 = date(2025, 1, 1)
DAY2 = date(2025, 1, 2)
DAY3 = date(2025, 1, 3)


# ---------------------------------------------------------------------------
# is_qualifying_activity — only ADVANCE/DEFEND count (Req 15.1, 15.4)
# ---------------------------------------------------------------------------

def test_advance_and_defend_are_qualifying():
    assert game.is_qualifying_activity("ADVANCE") is True
    assert game.is_qualifying_activity("DEFEND") is True


def test_open_and_none_are_not_qualifying():
    # The Streak is based on advance-or-defend, never raw app opens (Req 15.4).
    assert game.is_qualifying_activity("OPEN") is False
    assert game.is_qualifying_activity("NONE") is False
    # Any unrecognized value is treated as non-qualifying as well.
    assert game.is_qualifying_activity("WHATEVER") is False


# ---------------------------------------------------------------------------
# Req 15.1 — an advance-or-defend day counts toward the Streak
# ---------------------------------------------------------------------------

def test_first_advance_starts_the_streak_at_one():
    start = game.StreakState(last_active_date=None, count=0, freeze_tokens=0)
    after = game.register_activity(start, DAY1, "ADVANCE")
    assert after.count == 1
    assert after.last_active_date == DAY1
    assert after.freeze_tokens == 0


def test_consecutive_advance_days_increment_the_streak():
    state = game.StreakState(last_active_date=None, count=0, freeze_tokens=0)
    state = game.register_activity(state, DAY1, "ADVANCE")
    state = game.register_activity(state, DAY2, "ADVANCE")
    state = game.register_activity(state, DAY3, "ADVANCE")
    assert state.count == 3
    assert state.last_active_date == DAY3


def test_defend_day_also_counts_toward_the_streak():
    # DEFEND qualifies exactly like ADVANCE (Req 15.1).
    state = game.StreakState(last_active_date=DAY1, count=2, freeze_tokens=0)
    after = game.register_activity(state, DAY2, "DEFEND")
    assert after.count == 3
    assert after.last_active_date == DAY2


def test_second_qualifying_activity_same_day_does_not_double_count():
    # A day at or before last_active_date is already accounted for (Req 15.1).
    state = game.StreakState(last_active_date=DAY1, count=1, freeze_tokens=0)
    after = game.register_activity(state, DAY1, "ADVANCE")
    assert after.count == 1
    assert after.last_active_date == DAY1


def test_update_streak_increments_on_qualifying_activity_flag():
    # The lower-level helper increments when the qualifying flag is True.
    state = game.StreakState(last_active_date=DAY1, count=5, freeze_tokens=0)
    after = game.update_streak(state, DAY2, has_qualifying_activity=True)
    assert after.count == 6
    assert after.last_active_date == DAY2


# ---------------------------------------------------------------------------
# Req 15.2 — an inactive day with a Freeze_Token held preserves the Streak
# ---------------------------------------------------------------------------

def test_inactive_day_with_token_preserves_streak_and_consumes_one_token():
    state = game.StreakState(last_active_date=DAY1, count=3, freeze_tokens=1)
    after = game.update_streak(state, DAY2, has_qualifying_activity=False)
    # Streak length is preserved...
    assert after.count == 3
    # ...at the cost of exactly one Freeze_Token.
    assert after.freeze_tokens == 0
    assert after.last_active_date == DAY2


def test_raw_open_with_token_preserves_streak_like_an_inactive_day():
    # OPEN is non-qualifying, so it is preserved by a token just like inactivity.
    state = game.StreakState(last_active_date=DAY1, count=4, freeze_tokens=2)
    after = game.register_activity(state, DAY2, "OPEN")
    assert after.count == 4
    assert after.freeze_tokens == 1
    assert after.last_active_date == DAY2


# ---------------------------------------------------------------------------
# Req 15.3 — an inactive day with no token resets the Streak to zero
# ---------------------------------------------------------------------------

def test_inactive_day_with_no_token_resets_streak_to_zero():
    state = game.StreakState(last_active_date=DAY1, count=3, freeze_tokens=0)
    after = game.update_streak(state, DAY2, has_qualifying_activity=False)
    assert after.count == 0
    assert after.freeze_tokens == 0
    assert after.last_active_date == DAY2


def test_streak_resets_once_tokens_run_out_across_multiple_inactive_days():
    # One token covers the first inactive day; the next uncovered day resets.
    state = game.StreakState(last_active_date=DAY1, count=3, freeze_tokens=1)
    state = game.update_streak(state, DAY2, has_qualifying_activity=False)
    assert state.count == 3 and state.freeze_tokens == 0
    state = game.update_streak(state, DAY3, has_qualifying_activity=False)
    assert state.count == 0 and state.freeze_tokens == 0


# ---------------------------------------------------------------------------
# Req 15.4 — a raw OPEN is treated as an inactive day, not a streak day
# ---------------------------------------------------------------------------

def test_raw_open_without_token_resets_streak_like_inactivity():
    state = game.StreakState(last_active_date=DAY1, count=3, freeze_tokens=0)
    after = game.register_activity(state, DAY2, "OPEN")
    # OPEN never counts toward the Streak, so with no token it resets (Req 15.4).
    assert after.count == 0
    assert after.last_active_date == DAY2


def test_none_activity_is_treated_as_inactive():
    state = game.StreakState(last_active_date=DAY1, count=2, freeze_tokens=0)
    after = game.register_activity(state, DAY2, "NONE")
    assert after.count == 0
    assert after.last_active_date == DAY2


def test_update_streak_is_deterministic():
    state = game.StreakState(last_active_date=DAY1, count=3, freeze_tokens=1)
    a = game.update_streak(state, DAY2, has_qualifying_activity=False)
    b = game.update_streak(state, DAY2, has_qualifying_activity=False)
    assert a == b
