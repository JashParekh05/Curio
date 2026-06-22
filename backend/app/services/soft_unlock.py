"""Soft_Unlock pure decision core (Phase 3, Req 3 soft gating).

Soft_Unlock derives the advisory status the Learn surface shows for a learner's
topics and Levels. "Soft" is structural, not merely a convention: the status
values this module can produce are exactly ``available``, ``recommended``, and
``mastered`` -- there is intentionally NO ``locked`` (or otherwise blocking)
value, so nothing this core returns can ever stop the feed from serving the next
clip. A later Level is always navigable; only its *recommended* state changes as
the learner masters earlier topics (Req 3.3).

Following the repository convention (see ``coverage_view.py`` / ``placement.py``),
this module holds ONLY the pure decision logic: no DB, no clock, no global reads.
Every function is total and deterministic in its inputs and free of side effects
-- identical inputs always yield identical output and no Curriculum_Spine or
progression state is read or changed (Req 3.4). The thin, best-effort I/O shell
that assembles per-Level mastered flags from ``learning_progress`` and serves the
status to the frontend lives in ``progress.py``.

ASCII only.

Validates: Requirements 3.3, 3.4
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Status type
# ---------------------------------------------------------------------------

#: A topic's advisory Soft_Unlock status. ``available`` means always navigable
#: (the default, and the only non-mastered/non-next state), ``recommended``
#: marks the earliest unmastered topic as the next best place to focus, and
#: ``mastered`` marks a topic the learner has mastered. There is intentionally
#: NO ``locked`` value -- the scroll is never blocked (Req 3.3).
TopicUnlock = Literal["available", "recommended", "mastered"]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LevelProgress:
    """A Level's advisory progress summary.

    Attributes:
        ordinal: The Level's 1-based ordinal, echoed from the input.
        percent_complete: The whole-percent share of the Level's topics that are
            mastered, bounded in [0, 100]; 0 when the Level has no topics
            (Req 3.3).
        all_mastered: True iff every topic in a non-empty Level is mastered;
            vacuously False when the Level has no topics (Req 3.3).
    """

    ordinal: int
    percent_complete: int
    all_mastered: bool


# ---------------------------------------------------------------------------
# Topic status
# ---------------------------------------------------------------------------

def topic_unlock(mastered: bool, is_next_unmastered: bool) -> TopicUnlock:
    """Derive a single topic's advisory Soft_Unlock status.

    Returns ``mastered`` when the topic is mastered; otherwise ``recommended``
    when it is the earliest unmastered topic (``is_next_unmastered``); otherwise
    ``available``. The mastered flag takes precedence, so a mastered topic is
    never reported as the next recommended one. The return type has no locked or
    blocking member, so this function can never block feed progression (Req 3.3).

    Pure and total: defined for every combination of inputs and free of side
    effects, so identical inputs always yield identical output (Req 3.4).

    Args:
        mastered: True iff the topic is mastered.
        is_next_unmastered: True iff the topic is the earliest unmastered topic.

    Returns:
        The topic's advisory :data:`TopicUnlock` status.

    Validates: Requirements 3.3, 3.4
    """
    if mastered:
        return "mastered"
    if is_next_unmastered:
        return "recommended"
    return "available"


# ---------------------------------------------------------------------------
# Level progress
# ---------------------------------------------------------------------------

def level_progress(ordinal: int, topic_mastered_flags: list[bool]) -> LevelProgress:
    """Derive a Level's advisory progress from its per-topic mastered flags.

    The percent complete is ``round(100 * mastered / total)`` over the Level's
    topics, and 0 when the Level has no topics; it is therefore always bounded in
    [0, 100] and equals 100 if and only if every topic in a non-empty Level is
    mastered (Req 3.3). ``all_mastered`` is True iff every topic is mastered and
    the Level is non-empty -- vacuously False for an empty Level so an empty Level
    never reads as fully complete (Req 3.3).

    Pure and total: defined for any ordinal and any flag list (including the
    empty list) and free of side effects, so identical inputs always yield
    identical output (Req 3.4).

    Args:
        ordinal: The Level's 1-based ordinal, echoed into the result.
        topic_mastered_flags: One mastered flag per topic in the Level, in Level
            order.

    Returns:
        The Level's :class:`LevelProgress` summary.

    Validates: Requirements 3.3, 3.4
    """
    total = len(topic_mastered_flags)
    if total == 0:
        return LevelProgress(ordinal=ordinal, percent_complete=0, all_mastered=False)

    mastered = sum(1 for flag in topic_mastered_flags if flag)
    percent_complete = round(100 * mastered / total)
    all_mastered = mastered == total
    return LevelProgress(
        ordinal=ordinal,
        percent_complete=percent_complete,
        all_mastered=all_mastered,
    )
