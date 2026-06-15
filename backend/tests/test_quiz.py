"""Pure-helper tests for the quiz module: validation, grading, streak points,
and mastery aggregation. Mutation-spot-checked in the review step."""
from hypothesis import given, strategies as st

from app.services.quiz import (
    _validate_question,
    grade,
    points_for,
    summarize_mastery,
    POINTS_PER_CORRECT,
    MAX_STREAK_BONUS,
    MASTERED_THRESHOLD,
)


def _q(correct_index=0, options=None, question="What is X?", explanation="Because Y."):
    return {
        "question": question,
        "options": options if options is not None else ["a", "b", "c"],
        "correct_index": correct_index,
        "explanation": explanation,
    }


class TestValidateQuestion:
    def test_accepts_clean_question(self):
        out = _validate_question(_q())
        assert out["correct_index"] == 0 and len(out["options"]) == 3

    def test_rejects_non_dict(self):
        assert _validate_question("nope") is None
        assert _validate_question(None) is None

    def test_rejects_blank_prompt_or_explanation(self):
        assert _validate_question(_q(question="")) is None
        assert _validate_question(_q(explanation="  ")) is None

    def test_rejects_too_few_or_too_many_options(self):
        assert _validate_question(_q(options=["only one"], correct_index=0)) is None
        assert _validate_question(_q(options=["a", "b", "c", "d", "e"], correct_index=0)) is None

    def test_rejects_out_of_range_correct_index(self):
        assert _validate_question(_q(options=["a", "b"], correct_index=2)) is None
        assert _validate_question(_q(options=["a", "b"], correct_index=-1)) is None

    def test_rejects_non_integer_correct_index(self):
        assert _validate_question(_q(correct_index="two")) is None
        assert _validate_question({**_q(), "correct_index": None}) is None

    def test_drops_blank_options_then_revalidates_count(self):
        # one blank option among two -> only one real option -> rejected
        assert _validate_question(_q(options=["a", "  "], correct_index=0)) is None

    def test_strips_whitespace(self):
        out = _validate_question(_q(question="  Q  ", options=[" a ", "b"], correct_index=1))
        assert out["question"] == "Q" and out["options"][0] == "a"


class TestGrade:
    def test_correct_awards_points(self):
        correct, pts = grade(_q(correct_index=1), 1)
        assert correct is True and pts == POINTS_PER_CORRECT

    def test_incorrect_awards_zero(self):
        correct, pts = grade(_q(correct_index=1), 0)
        assert correct is False and pts == 0

    def test_bad_chosen_index_is_incorrect(self):
        assert grade(_q(), "x") == (False, 0)

    @given(
        correct_index=st.integers(min_value=0, max_value=3),
        chosen=st.integers(min_value=0, max_value=3),
        streak=st.integers(min_value=0, max_value=50),
    )
    def test_points_zero_unless_match(self, correct_index, chosen, streak):
        correct, pts = grade(_q(options=["a", "b", "c", "d"], correct_index=correct_index), chosen, streak)
        if chosen == correct_index:
            assert correct and pts == points_for(streak)
        else:
            assert not correct and pts == 0


class TestPointsFor:
    def test_base_at_zero_streak(self):
        assert points_for(0) == POINTS_PER_CORRECT

    def test_clamped_at_max(self):
        assert points_for(10_000) == POINTS_PER_CORRECT + MAX_STREAK_BONUS

    @given(a=st.integers(min_value=0, max_value=10_000), b=st.integers(min_value=0, max_value=10_000))
    def test_monotonic_non_decreasing(self, a, b):
        lo, hi = min(a, b), max(a, b)
        assert points_for(lo) <= points_for(hi)

    @given(streak=st.integers(min_value=-100, max_value=10_000))
    def test_never_exceeds_clamp_and_at_least_base(self, streak):
        p = points_for(streak)
        assert POINTS_PER_CORRECT <= p <= POINTS_PER_CORRECT + MAX_STREAK_BONUS


class TestSummarizeMastery:
    def test_empty(self):
        out = summarize_mastery([])
        assert out == {"topics": {}, "total_points": 0}

    def test_rates_and_total(self):
        results = [
            {"topic_slug": "t1", "correct": True, "points": 10},
            {"topic_slug": "t1", "correct": False, "points": 0},
            {"topic_slug": "t2", "correct": True, "points": 15},
        ]
        out = summarize_mastery(results)
        assert out["total_points"] == 25
        assert out["topics"]["t1"]["correct_rate"] == 0.5
        assert out["topics"]["t2"]["correct_rate"] == 1.0

    def test_mastered_threshold_boundary(self):
        # exactly at threshold counts as mastered; just below does not
        at = [{"topic_slug": "t", "correct": i < int(MASTERED_THRESHOLD * 10), "points": 0} for i in range(10)]
        assert summarize_mastery(at)["topics"]["t"]["mastered"] is True
        below = [{"topic_slug": "t", "correct": i < int(MASTERED_THRESHOLD * 10) - 1, "points": 0} for i in range(10)]
        assert summarize_mastery(below)["topics"]["t"]["mastered"] is False

    def test_ignores_rows_without_slug(self):
        out = summarize_mastery([{"correct": True, "points": 99}])
        assert out == {"topics": {}, "total_points": 0}

    def test_additive_across_attempts(self):
        results = [{"topic_slug": "t", "correct": True, "points": 10} for _ in range(3)]
        out = summarize_mastery(results)
        assert out["topics"]["t"]["answered"] == 3 and out["topics"]["t"]["points"] == 30
