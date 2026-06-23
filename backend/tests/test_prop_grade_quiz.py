"""Property-based test for deterministic per-concept quiz grading.

# Feature: adaptive-learning-game, Property 2: Per-concept grading partitions concepts and derives the score fraction

For any set of quiz questions (each carrying a ``concept_tag``) and any
corresponding answers, ``grade_quiz`` deterministically partitions the answered
concepts such that every answered concept appears in ``correct_concepts`` or
``missed_concepts`` (disjoint and jointly covering), any concept with at least
one incorrect answer appears in ``missed_concepts``, and ``score_pct`` equals
``correct_count / total`` and lies in ``[0, 1]``.

Validates: Requirements 4.1, 4.3, 4.4
"""
from hypothesis import given, settings, strategies as st

from app.services.game import grade_quiz

# A small fixed pool of Concept_Tags so tags recur across questions, which is
# what exercises the "missed if any one wrong" partition rule (Req 4.2, 4.3).
_TAGS = ["recursion", "base-case", "pruning", "state-space", "ordering"]


@st.composite
def _quiz_and_answers(draw):
    """Generate a non-empty quiz of tagged MCQs plus a chosen answer per
    question. Each question has 2-5 options with a valid ``correct_index``; each
    answer is an option index (which may or may not match the correct one)."""
    n = draw(st.integers(min_value=1, max_value=12))
    questions: list[dict] = []
    answers: list[int] = []
    for _ in range(n):
        num_options = draw(st.integers(min_value=2, max_value=5))
        correct_index = draw(st.integers(min_value=0, max_value=num_options - 1))
        tag = draw(st.sampled_from(_TAGS))
        questions.append(
            {
                "concept_tag": tag,
                "correct_index": correct_index,
                "options": [f"opt{i}" for i in range(num_options)],
            }
        )
        # Chosen index within the option range so both correct and incorrect
        # answers are realised across examples.
        answers.append(draw(st.integers(min_value=0, max_value=num_options - 1)))
    return questions, answers


class TestPerConceptGradingPartition:
    @settings(max_examples=200)
    @given(data=_quiz_and_answers())
    def test_concepts_partitioned_and_score_derived(self, data):
        questions, answers = data
        result = grade_quiz(questions, answers)

        all_tags = {str(q["concept_tag"]) for q in questions}
        missed = set(result.missed_concepts)
        correct = set(result.correct_concepts)

        # Disjoint: no concept is both missed and correct.
        assert missed.isdisjoint(correct)
        # Covering: together they account for every Concept_Tag in the quiz.
        assert missed | correct == all_tags
        # No duplicates within each returned list.
        assert len(result.missed_concepts) == len(missed)
        assert len(result.correct_concepts) == len(correct)

        # Missed-on-any-wrong rule: a tag is missed iff at least one question
        # carrying it was answered incorrectly; correct iff all were right.
        wrong_tags = set()
        right_only_tags = set()
        per_tag_any_wrong: dict[str, bool] = {}
        for q, a in zip(questions, answers):
            tag = str(q["concept_tag"])
            is_correct = a == q["correct_index"]
            per_tag_any_wrong[tag] = per_tag_any_wrong.get(tag, False) or (not is_correct)
        for tag, any_wrong in per_tag_any_wrong.items():
            (wrong_tags if any_wrong else right_only_tags).add(tag)
        assert missed == wrong_tags
        assert correct == right_only_tags

        # Score fraction equals correct/total and lies in [0, 1].
        total = len(questions)
        expected_correct = sum(1 for q, a in zip(questions, answers) if a == q["correct_index"])
        assert result.total == total
        assert result.correct == expected_correct
        assert result.score_pct == round(expected_correct / total, 4)
        assert 0.0 <= result.score_pct <= 1.0

    @settings(max_examples=100)
    @given(data=_quiz_and_answers())
    def test_grading_is_deterministic(self, data):
        questions, answers = data
        first = grade_quiz(questions, answers)
        second = grade_quiz(questions, answers)
        assert first == second

    def test_zero_question_quiz_rejected(self):
        # A zero-question quiz cannot be banded and must be rejected (Req 4.7).
        import pytest

        with pytest.raises(ValueError):
            grade_quiz([], [])
