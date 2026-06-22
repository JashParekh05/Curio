"""Property-based test for the Mastery_Compute pure core.

# Feature: structured-learn-curriculum, Property 7: Mastery is bounded, consistent, and monotonic (P3.1-P3.6)

``topic_mastery`` blends a quiz-correctness rate and a clip-completion rate into a
single bounded mastery score, a ``mastered`` flag, and a coarse status. This module
exercises the property bundle P3.1-P3.6: the score is always in ``[0, 1]`` for any
integer inputs (including zero denominators, negatives, and numerator > denominator),
``mastered`` tracks the ``>= 0.7`` threshold exactly, the score is monotonic
non-decreasing in quiz correctness and clip completion at a fixed denominator, the
status truth table holds, the corner signals collapse to ``1.0`` / ``0.0``, and the
function is deterministic and total.

Validates: Requirements 3.1, 3.4
"""
from __future__ import annotations

from hypothesis import given, settings, strategies as st

from app.services.mastery import (
    MASTERY_THRESHOLD,
    topic_mastery,
)

# Arbitrary integers spanning zero denominators, negatives, and
# numerator > denominator. Bounded magnitude keeps generation cheap while still
# covering every degenerate region the core must clamp.
_ints = st.integers(min_value=-50, max_value=50)
_slugs = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyz-", min_size=1, max_size=12
)


class TestMasteryProperties:
    @settings(max_examples=100)
    @given(
        slug=_slugs,
        quiz_correct=_ints,
        quiz_answered=_ints,
        clips_completed=_ints,
        clips_seen=_ints,
    )
    def test_score_is_bounded(
        self, slug, quiz_correct, quiz_answered, clips_completed, clips_seen
    ):
        # P3.1: mastery_score is always in [0, 1] for any integer inputs,
        # including zero denominators, negatives, and numerator > denominator.
        result = topic_mastery(
            slug, quiz_correct, quiz_answered, clips_completed, clips_seen
        )
        assert 0.0 <= result.mastery_score <= 1.0
        assert result.topic_slug == slug

    @settings(max_examples=100)
    @given(
        slug=_slugs,
        quiz_correct=_ints,
        quiz_answered=_ints,
        clips_completed=_ints,
        clips_seen=_ints,
    )
    def test_mastered_iff_at_or_above_threshold(
        self, slug, quiz_correct, quiz_answered, clips_completed, clips_seen
    ):
        # P3.2: mastered is True iff mastery_score >= MASTERY_THRESHOLD.
        result = topic_mastery(
            slug, quiz_correct, quiz_answered, clips_completed, clips_seen
        )
        assert result.mastered == (result.mastery_score >= MASTERY_THRESHOLD)

    @settings(max_examples=100)
    @given(
        slug=_slugs,
        quiz_correct=_ints,
        delta_quiz=st.integers(min_value=0, max_value=50),
        quiz_answered=_ints,
        clips_completed=_ints,
        delta_clips=st.integers(min_value=0, max_value=50),
        clips_seen=_ints,
    )
    def test_score_is_monotonic(
        self,
        slug,
        quiz_correct,
        delta_quiz,
        quiz_answered,
        clips_completed,
        delta_clips,
        clips_seen,
    ):
        # P3.3: increasing quiz_correct (fixed quiz_answered) or clips_completed
        # (fixed clips_seen) never decreases mastery_score.
        base = topic_mastery(
            slug, quiz_correct, quiz_answered, clips_completed, clips_seen
        )
        more_quiz = topic_mastery(
            slug, quiz_correct + delta_quiz, quiz_answered, clips_completed, clips_seen
        )
        more_clips = topic_mastery(
            slug, quiz_correct, quiz_answered, clips_completed + delta_clips, clips_seen
        )
        assert more_quiz.mastery_score >= base.mastery_score
        assert more_clips.mastery_score >= base.mastery_score

    @settings(max_examples=100)
    @given(
        slug=_slugs,
        quiz_correct=_ints,
        quiz_answered=_ints,
        clips_completed=_ints,
        clips_seen=_ints,
    )
    def test_status_truth_table(
        self, slug, quiz_correct, quiz_answered, clips_completed, clips_seen
    ):
        # P3.4: status == 'not_started' iff quiz_answered == 0 and clips_seen == 0;
        # status == 'mastered' iff mastered.
        result = topic_mastery(
            slug, quiz_correct, quiz_answered, clips_completed, clips_seen
        )
        not_started = quiz_answered == 0 and clips_seen == 0
        assert (result.status == "not_started") == not_started
        assert (result.status == "mastered") == result.mastered
        # The three statuses are mutually exclusive and exhaustive.
        assert result.status in ("not_started", "in_progress", "mastered")

    @settings(max_examples=100)
    @given(
        slug=_slugs,
        quiz_answered=st.integers(min_value=1, max_value=50),
        clips_seen=st.integers(min_value=1, max_value=50),
    )
    def test_corner_signals(self, slug, quiz_answered, clips_seen):
        # P3.5: perfect quiz + full completion -> 1.0; zero signal -> 0.0.
        perfect = topic_mastery(
            slug, quiz_answered, quiz_answered, clips_seen, clips_seen
        )
        assert perfect.mastery_score == 1.0
        assert perfect.mastered is True

        zero = topic_mastery(slug, 0, quiz_answered, 0, clips_seen)
        assert zero.mastery_score == 0.0
        assert zero.mastered is False

    @settings(max_examples=100)
    @given(
        slug=_slugs,
        quiz_correct=_ints,
        quiz_answered=_ints,
        clips_completed=_ints,
        clips_seen=_ints,
    )
    def test_determinism(
        self, slug, quiz_correct, quiz_answered, clips_completed, clips_seen
    ):
        # P3.6: identical inputs always produce identical output; total, never raises.
        first = topic_mastery(
            slug, quiz_correct, quiz_answered, clips_completed, clips_seen
        )
        second = topic_mastery(
            slug, quiz_correct, quiz_answered, clips_completed, clips_seen
        )
        assert first == second
