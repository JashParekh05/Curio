"""Unit tests for spaced decay scheduling and Defend restoration (Phase 2, Req 14).

These exercise the pure, deterministic decay helpers in
``app.services.game``. Every transition takes ``now`` (and dates) explicitly, so
the assertions are wall-clock independent.

Coverage:

- **Interval progression (Req 14.1).** ``schedule_next_due`` / ``mark_mastered``
  walk the Decay_Schedule 1d → 3d → 7d → 16d across successive masteries and
  clamp at the final 16-day interval.
- **DECAYING flagging (Req 14.2).** ``is_decaying`` / ``refresh_decay_state``
  flag a past-due MASTERED Node as DECAYING and leave not-yet-due or
  non-mastered Nodes unchanged.
- **MASTERED restoration on a passed Defend (Req 14.3).** ``apply_defend``
  restores a decaying Node to MASTERED and advances it to the next interval when
  the Defend re-quiz passes (CLIMB band), and holds it DECAYING when it fails.

Validates: Requirements 14.1, 14.2, 14.3
"""
from datetime import datetime, timedelta

from app.services import game

# A fixed reference instant so every schedule is computed deterministically.
NOW = datetime(2025, 1, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# Req 14.1 — Decay_Schedule interval progression (1d → 3d → 7d → 16d, clamped)
# ---------------------------------------------------------------------------

def test_decay_schedule_days_are_the_canonical_intervals():
    assert game.DECAY_SCHEDULE_DAYS == (1, 3, 7, 16)


def test_schedule_next_due_first_interval_is_one_day():
    sched = game.schedule_next_due(decay_step=0, now=NOW)
    assert sched.decay_due_at == NOW + timedelta(days=1)
    assert sched.decay_step == 1


def test_schedule_next_due_progresses_one_three_seven_sixteen():
    # step 0 → +1d (advance to 1)
    s0 = game.schedule_next_due(decay_step=0, now=NOW)
    assert s0.decay_due_at == NOW + timedelta(days=1)
    assert s0.decay_step == 1

    # step 1 → +3d (advance to 2)
    s1 = game.schedule_next_due(decay_step=1, now=NOW)
    assert s1.decay_due_at == NOW + timedelta(days=3)
    assert s1.decay_step == 2

    # step 2 → +7d (advance to 3)
    s2 = game.schedule_next_due(decay_step=2, now=NOW)
    assert s2.decay_due_at == NOW + timedelta(days=7)
    assert s2.decay_step == 3

    # step 3 → +16d (clamp at 3)
    s3 = game.schedule_next_due(decay_step=3, now=NOW)
    assert s3.decay_due_at == NOW + timedelta(days=16)
    assert s3.decay_step == 3


def test_schedule_next_due_clamps_at_sixteen_days_beyond_last_step():
    # Steps past the table hold at the widest 16-day spacing.
    sched = game.schedule_next_due(decay_step=99, now=NOW)
    assert sched.decay_due_at == NOW + timedelta(days=16)
    assert sched.decay_step == 3


def test_schedule_next_due_clamps_negative_step_to_first_interval():
    sched = game.schedule_next_due(decay_step=-5, now=NOW)
    assert sched.decay_due_at == NOW + timedelta(days=1)
    assert sched.decay_step == 1


def test_schedule_next_due_is_deterministic():
    a = game.schedule_next_due(decay_step=1, now=NOW)
    b = game.schedule_next_due(decay_step=1, now=NOW)
    assert a == b


def test_mark_mastered_walks_the_full_interval_progression():
    """Successive masteries advance through 1d → 3d → 7d → 16d → 16d."""
    expected_offsets = [1, 3, 7, 16, 16]
    step = 0
    for offset in expected_offsets:
        state = game.mark_mastered(decay_step=step, now=NOW)
        assert state.state == "MASTERED"
        assert state.decay_due_at == NOW + timedelta(days=offset)
        step = state.decay_step
    # After the progression the step has clamped at the final index.
    assert step == game._DECAY_LAST_STEP


# ---------------------------------------------------------------------------
# Req 14.2 — past-due mastered Node flagged DECAYING
# ---------------------------------------------------------------------------

def test_is_decaying_false_when_no_schedule():
    assert game.is_decaying(decay_due_at=None, now=NOW) is False


def test_is_decaying_false_before_due_time():
    due = NOW + timedelta(days=1)
    assert game.is_decaying(decay_due_at=due, now=NOW) is False


def test_is_decaying_true_at_and_after_due_time():
    assert game.is_decaying(decay_due_at=NOW, now=NOW) is True
    later = NOW + timedelta(seconds=1)
    assert game.is_decaying(decay_due_at=NOW, now=later) is True


def test_refresh_decay_state_flags_past_due_mastered_as_decaying():
    mastered = game.mark_mastered(decay_step=0, now=NOW)
    # Advance the clock one interval past the due time.
    past_due = mastered.decay_due_at + timedelta(hours=1)

    refreshed = game.refresh_decay_state(
        state=mastered.state,
        decay_due_at=mastered.decay_due_at,
        decay_step=mastered.decay_step,
        now=past_due,
    )
    assert refreshed.state == "DECAYING"
    # Schedule fields preserved so a Defend resumes from the correct step.
    assert refreshed.decay_due_at == mastered.decay_due_at
    assert refreshed.decay_step == mastered.decay_step


def test_refresh_decay_state_leaves_not_yet_due_mastered_unchanged():
    mastered = game.mark_mastered(decay_step=0, now=NOW)
    refreshed = game.refresh_decay_state(
        state=mastered.state,
        decay_due_at=mastered.decay_due_at,
        decay_step=mastered.decay_step,
        now=NOW,  # before the due time
    )
    assert refreshed.state == "MASTERED"
    assert refreshed.decay_due_at == mastered.decay_due_at
    assert refreshed.decay_step == mastered.decay_step


def test_refresh_decay_state_is_idempotent_for_already_decaying():
    mastered = game.mark_mastered(decay_step=0, now=NOW)
    past_due = mastered.decay_due_at + timedelta(hours=1)
    once = game.refresh_decay_state(
        state="DECAYING",
        decay_due_at=mastered.decay_due_at,
        decay_step=mastered.decay_step,
        now=past_due,
    )
    # A non-MASTERED state is returned unchanged.
    assert once.state == "DECAYING"
    assert once.decay_due_at == mastered.decay_due_at
    assert once.decay_step == mastered.decay_step


def test_refresh_decay_state_leaves_other_states_unchanged():
    for state in ("LOCKED", "NEXT", "CLEARED"):
        refreshed = game.refresh_decay_state(
            state=state,
            decay_due_at=NOW - timedelta(days=1),
            decay_step=2,
            now=NOW,
        )
        assert refreshed.state == state
        assert refreshed.decay_step == 2


# ---------------------------------------------------------------------------
# Req 14.3 — a passed Defend restores MASTERED and advances the interval
# ---------------------------------------------------------------------------

def test_defend_passed_only_in_climb_band():
    # CLIMB band is score > 0.70.
    assert game.defend_passed(0.71) is True
    assert game.defend_passed(1.0) is True
    # REINFORCE / DESCEND bands do not pass a Defend.
    assert game.defend_passed(0.70) is False
    assert game.defend_passed(0.40) is False
    assert game.defend_passed(0.0) is False


def test_apply_defend_pass_restores_mastered_and_advances_interval():
    # A node decaying at step 1 (its next interval is 3 days).
    defend_now = NOW + timedelta(days=10)
    state = game.apply_defend(score_pct=1.0, decay_step=1, now=defend_now)

    assert state.state == "MASTERED"
    # Advanced to the step-1 interval (3 days) from the Defend instant.
    assert state.decay_due_at == defend_now + timedelta(days=3)
    assert state.decay_step == 2


def test_apply_defend_pass_from_first_step_schedules_one_day():
    defend_now = NOW + timedelta(days=2)
    state = game.apply_defend(score_pct=0.9, decay_step=0, now=defend_now)
    assert state.state == "MASTERED"
    assert state.decay_due_at == defend_now + timedelta(days=1)
    assert state.decay_step == 1


def test_apply_defend_fail_holds_node_decaying_at_same_step():
    defend_now = NOW + timedelta(days=10)
    state = game.apply_defend(score_pct=0.3, decay_step=2, now=defend_now)

    assert state.state == "DECAYING"
    # Still due now so the learner can retry, step unchanged.
    assert state.decay_due_at == defend_now
    assert state.decay_step == 2


def test_apply_defend_fail_clamps_out_of_range_step():
    defend_now = NOW + timedelta(days=10)
    state = game.apply_defend(score_pct=0.0, decay_step=99, now=defend_now)
    assert state.state == "DECAYING"
    assert state.decay_step == game._DECAY_LAST_STEP


def test_defend_pass_after_decay_restores_and_progresses_to_next_interval():
    """End-to-end: master → decay past due → pass Defend → re-mastered on next interval."""
    # 1) Master at step 0 (due in 1 day).
    mastered = game.mark_mastered(decay_step=0, now=NOW)
    assert mastered.state == "MASTERED"
    assert mastered.decay_step == 1

    # 2) Clock passes the due time → flagged DECAYING.
    past_due = mastered.decay_due_at + timedelta(hours=1)
    decaying = game.refresh_decay_state(
        state=mastered.state,
        decay_due_at=mastered.decay_due_at,
        decay_step=mastered.decay_step,
        now=past_due,
    )
    assert decaying.state == "DECAYING"

    # 3) Learner passes the Defend → restored to MASTERED, advanced to 3-day interval.
    restored = game.apply_defend(
        score_pct=1.0, decay_step=decaying.decay_step, now=past_due
    )
    assert restored.state == "MASTERED"
    assert restored.decay_due_at == past_due + timedelta(days=3)
    assert restored.decay_step == 2
