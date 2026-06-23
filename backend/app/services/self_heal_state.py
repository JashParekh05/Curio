"""Bounded self-heal retry policy + in-process attempt tracker.

The path feed re-triggers background generation for any empty, non-generating
topic. Without a cap or cooldown this loops every ~4s poll forever and drains
the YouTube Data API quota. This module holds the policy constants, the pure
decision functions (no DB, no clock, no globals read), and a tiny in-process
attempt tracker that survives the ``generating_slugs`` lifecycle.

Mirrors the existing best-effort in-process guards ``_quiz_generating``
(quiz.py) and ``_expanding`` (topic_expansion.py). Best-effort throughout;
reset on restart by design (a restart is exactly when a legitimate re-heal is
allowed). ASCII only.
"""
import time

# Each self-heal attempt runs the section pipeline (~4 YouTube searches x 100
# units = ~400 units, plus ~4 paid TranscriptAPI credits) against a 10,000
# units/day free quota. Capping attempts per slug bounds worst-case spend before
# a topic goes terminal, leaving headroom for many distinct topics in a day.
MAX_SELF_HEAL_ATTEMPTS = 2
# Cooldown between attempts for the same slug. The feed polls every ~4s; a 5-min
# window means a failing topic is retried at most once per 5 min instead of
# every poll (~75x fewer triggers), and reaches the cap after ~10 min.
SELF_HEAL_COOLDOWN_SECONDS = 300

# slug -> (attempt_count, last_attempt_monotonic_time). Best-effort, in-process;
# reset on restart by design (a restart is when a legitimate re-heal is allowed).
_self_heal_attempts: dict[str, tuple[int, float]] = {}


def should_self_heal(has_clips: bool, is_generating: bool,
                     attempts: int, last_attempt_age_seconds: float | None) -> bool:
    """Pure policy: trigger background generation for an empty topic only when
    within the retry budget (under the attempt cap AND past the cooldown).
    last_attempt_age_seconds is None when there is no prior attempt."""
    if has_clips or is_generating:
        return False
    if attempts >= MAX_SELF_HEAL_ATTEMPTS:
        return False
    if attempts > 0 and last_attempt_age_seconds is not None \
            and last_attempt_age_seconds < SELF_HEAL_COOLDOWN_SECONDS:
        return False
    return True


def is_terminal_failed(has_clips: bool, is_generating: bool, attempts: int) -> bool:
    """Pure: a topic is terminal-failed when it is out of budget and still empty."""
    return (not has_clips) and (not is_generating) and attempts >= MAX_SELF_HEAL_ATTEMPTS


def read(slug: str) -> tuple[int, float | None]:
    """(attempts, age_seconds_since_last_attempt | None) for a slug."""
    entry = _self_heal_attempts.get(slug)
    if entry is None:
        return 0, None
    count, last = entry
    return count, time.monotonic() - last


def record_attempt(slug: str) -> None:
    """Record that a failed/empty generation attempt occurred for slug."""
    count, _ = _self_heal_attempts.get(slug, (0, 0.0))
    _self_heal_attempts[slug] = (count + 1, time.monotonic())


def clear(slug: str) -> None:
    """Clear tracking after a successful generation (>=1 clip stored)."""
    _self_heal_attempts.pop(slug, None)
