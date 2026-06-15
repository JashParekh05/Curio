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


# --- task 4: prompt building + judge gating --------------------------------

from app.services import quiz as quiz_mod
from app.services.quiz import _build_question_prompt, _vet_questions


class TestBuildQuestionPrompt:
    def test_instructs_mcq_only_and_anti_trivia(self):
        p = _build_question_prompt("Binary Search Trees",
                                   [{"title": "What is a BST", "description": "ordered tree"}],
                                   ["a transcript excerpt"])
        assert "MULTIPLE-CHOICE" in p
        assert "Do NOT produce true/false or open-ended" in p
        assert "NOT trivia about the video" in p

    def test_includes_sections_and_excerpt(self):
        p = _build_question_prompt("T", [{"title": "Sec", "description": "desc"}], ["hello world"])
        assert "Sec" in p and "desc" in p and "hello world" in p

    def test_handles_empty_inputs(self):
        p = _build_question_prompt("T", [], [])
        assert "no section outline available" in p

    def test_truncates_excerpt_to_budget(self):
        long = "x" * 5000
        p = _build_question_prompt("T", [], [long])
        # the excerpt is capped; the full 5000-char blob is not embedded whole
        assert "x" * 5000 not in p


class TestVetQuestions:
    def _good(self, i=0):
        return {"question": f"Q{i}?", "options": ["a", "b", "c"], "correct_index": 0, "explanation": "why"}

    def test_keeps_only_judge_approved_valid_questions(self, monkeypatch):
        # q0 approved, q1 flagged by judge, q2 invalid (2 correct -> rejected by validator)
        raw = [self._good(0), self._good(1), {"question": "bad", "options": ["a"], "correct_index": 0, "explanation": "x"}]
        def fake_judge(topic, q):
            return {"ok": q["question"] != "Q1?", "issue": "" if q["question"] != "Q1?" else "ambiguous"}
        monkeypatch.setattr(quiz_mod, "_judge_question", fake_judge)
        kept = _vet_questions("T", raw)
        assert [q["question"] for q in kept] == ["Q0?"]

    def test_invalid_questions_dropped_before_judging(self, monkeypatch):
        calls = {"n": 0}
        monkeypatch.setattr(quiz_mod, "_judge_question",
                            lambda t, q: calls.__setitem__("n", calls["n"] + 1) or {"ok": True, "issue": ""})
        # single-option question is invalid -> judge never called for it
        _vet_questions("T", [{"question": "x", "options": ["only"], "correct_index": 0, "explanation": "e"}])
        assert calls["n"] == 0

    def test_judge_failure_drops_only_that_question(self, monkeypatch):
        def flaky_judge(topic, q):
            if q["question"] == "Q1?":
                raise RuntimeError("judge down")
            return {"ok": True, "issue": ""}
        monkeypatch.setattr(quiz_mod, "_judge_question", flaky_judge)
        kept = _vet_questions("T", [self._good(0), self._good(1), self._good(2)])
        assert [q["question"] for q in kept] == ["Q0?", "Q2?"]

    def test_empty_input(self):
        assert _vet_questions("T", []) == []
        assert _vet_questions("T", None) == []


# --- task 5: generate_and_store_questions orchestration (fake DB) ----------

from app.services.quiz import generate_and_store_questions
from tests.conftest import FakeDB


def _patch_quiz_db(monkeypatch, store, fail=None):
    db = FakeDB(store=store, fail=fail or set())
    import app.db.supabase as supa
    monkeypatch.setattr(supa, "get_client", lambda: db)
    return db


def _store(existing_questions=None, sections=None, clips=None):
    return {
        "quiz_questions": existing_questions or [],
        "topic_sections": sections or [{"topic_slug": "t", "title": "Sec", "description": "d", "section_index": 0}],
        "clips": clips or [{"topic_slug": "t", "transcript": "some transcript"}],
    }


class TestGenerateAndStoreQuestions:
    def _vetted(self):
        return [{"question": "Q?", "options": ["a", "b"], "correct_index": 0, "explanation": "e"}]

    def test_stores_vetted_questions(self, monkeypatch):
        db = _patch_quiz_db(monkeypatch, _store())
        monkeypatch.setattr(quiz_mod, "_generate_questions", lambda *a, **k: [{"raw": 1}])
        monkeypatch.setattr(quiz_mod, "_vet_questions", lambda t, raw: self._vetted())
        n = generate_and_store_questions("t", "Topic")
        assert n == 1
        inserted = [p for tbl, p in db.rec["inserts"] if tbl == "quiz_questions"]
        assert len(inserted) == 1
        assert inserted[0]["topic_slug"] == "t" and inserted[0]["question"] == "Q?"

    def test_idempotent_skip_when_cached(self, monkeypatch):
        _patch_quiz_db(monkeypatch, _store(existing_questions=[{"id": "q1", "topic_slug": "t"}]))
        called = {"gen": False}
        monkeypatch.setattr(quiz_mod, "_generate_questions",
                            lambda *a, **k: called.__setitem__("gen", True) or [])
        assert generate_and_store_questions("t", "Topic") == 0
        assert called["gen"] is False

    def test_nothing_to_generate_from(self, monkeypatch):
        _patch_quiz_db(monkeypatch, {"quiz_questions": [], "topic_sections": [], "clips": []})
        assert generate_and_store_questions("t", "Topic") == 0

    def test_generation_failure_never_raises(self, monkeypatch):
        _patch_quiz_db(monkeypatch, _store())
        def boom(*a, **k):
            raise RuntimeError("LLM down")
        monkeypatch.setattr(quiz_mod, "_generate_questions", boom)
        assert generate_and_store_questions("t", "Topic") == 0  # no exception

    def test_no_questions_pass_gate(self, monkeypatch):
        _patch_quiz_db(monkeypatch, _store())
        monkeypatch.setattr(quiz_mod, "_generate_questions", lambda *a, **k: [{"raw": 1}])
        monkeypatch.setattr(quiz_mod, "_vet_questions", lambda t, raw: [])
        assert generate_and_store_questions("t", "Topic") == 0
