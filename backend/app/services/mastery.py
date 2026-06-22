"""Mastery_Compute pure decision core (Phase 3, Decision 3 / Req 3).

Mastery_Compute combines two per-topic learning signals -- quiz correctness and
clip completion -- into a single bounded mastery score, a ``mastered`` flag, and a
coarse progression status. It reuses the recommendation agent's ``>= 0.7``
"mastered" bar and the quiz module's mastery semantics so the signal stays
consistent across the app.

Following the repository convention (see ``placement.py`` /
``reseed_prioritizer.py`` / ``coverage_view.py``), this module holds ONLY the pure
decision logic: no DB, no clock, no global reads. Every function is deterministic
in its inputs and free of side effects. It is total -- the quiz and completion
rates clamp on zero-denominator, negative, or otherwise out-of-range input, and
the final score is clamped to ``[0, 1]`` -- so malformed stored counts degrade to
a safe default instead of raising into the request path (Req 4.1, 4.4). Because
every score is clamped here, a ``learning_progress.mastery_score`` persisted by the
shell can never violate the database ``check (>= 0 and <= 1)`` constraint
(Req 5.4).

The thin shell (``app/services/progress.py`` + a store) reads ``quiz_results``
correct counts (via the existing ``summarize_mastery`` shape) and ``clip_events``
completion counts, calls :func:`topic_mastery`, and upserts a
``learning_progress`` row best-effort, off the request path.

ASCII only.

Validates: Requirements 3.1, 3.4, 5.4
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Policy constants
# ---------------------------------------------------------------------------

#: A topic is considered "mastered" at or above this score. Shared with the
#: recommendation agent's mastered bar so the signal is consistent app-wide.
MASTERY_THRESHOLD: float = 0.7

#: Weight of the quiz-correctness rate in the blended mastery score. Quiz
#: correctness dominates; clip completion supports.
QUIZ_WEIGHT: float = 0.6

#: Weight of the clip-completion rate in the blended mastery score.
COMPLETION_WEIGHT: float = 0.4

#: The coarse progression status of a topic.
TopicStatus = Literal["not_started", "in_progress", "mastered"]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TopicMastery:
    """Per-topic mastery derived from quiz correctness and clip completion.

    Attributes:
        topic_slug: The Topic's stable slug; identifies the row this mastery
            belongs to.
        mastery_score: The blended mastery in ``[0, 1]``, rounded to 4 decimal
            places.
        status: The coarse progression status: ``not_started`` when nothing has
            been answered or seen, ``mastered`` when the score meets the
            threshold, else ``in_progress``.
        mastered: True iff ``mastery_score >= MASTERY_THRESHOLD``.
    """

    topic_slug: str
    mastery_score: float
    status: TopicStatus
    mastered: bool


# ---------------------------------------------------------------------------
# Rate helper
# ---------------------------------------------------------------------------

def _rate(numerator: int, denominator: int) -> float:
    """A success rate in ``[0, 1]``, total over any integer inputs.

    Returns 0.0 when the denominator is not positive (nothing answered/seen), so
    a zero or negative denominator never raises. Otherwise the ratio is clamped to
    ``[0, 1]`` so a negative numerator floors at 0.0 and ``numerator > denominator``
    caps at 1.0.
    """
    if denominator <= 0:
        return 0.0
    rate = numerator / denominator
    if rate < 0.0:
        return 0.0
    if rate > 1.0:
        return 1.0
    return rate


# ---------------------------------------------------------------------------
# Mastery
# ---------------------------------------------------------------------------

def topic_mastery(
    topic_slug: str,
    quiz_correct: int,
    quiz_answered: int,
    clips_completed: int,
    clips_seen: int,
) -> TopicMastery:
    """Compute per-topic mastery from quiz and completion signals.

    The quiz rate is ``quiz_correct / quiz_answered`` (0 when nothing answered) and
    the completion rate is ``clips_completed / clips_seen`` (0 when nothing seen);
    each rate is clamped to ``[0, 1]`` so negative or out-of-range counts degrade
    safely (Req 3.1). The blended ``mastery_score`` is
    ``QUIZ_WEIGHT * quiz_rate + COMPLETION_WEIGHT * completion_rate``, clamped to
    ``[0, 1]`` and rounded to 4 decimal places. A topic is ``mastered`` iff its
    score is at least :data:`MASTERY_THRESHOLD` (Req 3.1). The status is
    ``not_started`` exactly when nothing has been answered and nothing has been
    seen, ``mastered`` exactly when the mastered flag is set, else ``in_progress``
    (Req 3.1).

    Because each rate is monotonic non-decreasing in its numerator at a fixed
    denominator and the score is a non-negative weighted sum, the score never
    decreases as ``quiz_correct`` (fixed ``quiz_answered``) or ``clips_completed``
    (fixed ``clips_seen``) rises. With ``QUIZ_WEIGHT + COMPLETION_WEIGHT == 1.0``, a
    perfect quiz and full completion yield ``1.0`` and a zero signal yields ``0.0``.

    Pure and deterministic: identical inputs always produce identical output; no
    external service is accessed and no state is changed (Req 3.4). Total: never
    raises on any integer inputs, including zero denominators and negatives
    (Req 5.4 -- the clamped score keeps the persisted ``mastery_score`` within the
    DB check constraint).

    Args:
        topic_slug: The Topic's stable slug.
        quiz_correct: Number of quiz questions answered correctly for this topic.
        quiz_answered: Number of quiz questions answered for this topic.
        clips_completed: Number of clips completed for this topic.
        clips_seen: Number of clips seen for this topic.

    Returns:
        The :class:`TopicMastery` for the topic.

    Validates: Requirements 3.1, 3.4, 5.4
    """
    quiz_rate = _rate(quiz_correct, quiz_answered)
    completion_rate = _rate(clips_completed, clips_seen)

    raw_score = QUIZ_WEIGHT * quiz_rate + COMPLETION_WEIGHT * completion_rate
    if raw_score < 0.0:
        raw_score = 0.0
    elif raw_score > 1.0:
        raw_score = 1.0
    mastery_score = round(raw_score, 4)

    mastered = mastery_score >= MASTERY_THRESHOLD

    if quiz_answered == 0 and clips_seen == 0:
        status: TopicStatus = "not_started"
    elif mastered:
        status = "mastered"
    else:
        status = "in_progress"

    return TopicMastery(
        topic_slug=topic_slug,
        mastery_score=mastery_score,
        status=status,
        mastered=mastered,
    )
