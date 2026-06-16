"""Fake-DB tests for the /api/quiz handlers, exercised directly (no HTTP)."""
import asyncio

import pytest
from fastapi import BackgroundTasks, HTTPException

import app.api.quiz as quiz_api
from app.api.quiz import get_quiz, get_mastery, answer_question, QuizAnswer
from app.services.quiz import points_for
from tests.conftest import FakeDB


def _patch_db(monkeypatch, store, fail=None):
    db = FakeDB(store=store, fail=fail or set())
    monkeypatch.setattr(quiz_api, "get_client", lambda: db)
    return db


def _question(qid="q1", correct_index=0):
    return {
        "id": qid, "topic_slug": "t", "question": "Q?",
        "options": ["a", "b", "c"], "correct_index": correct_index, "explanation": "why",
    }


class TestGetQuiz:
    def test_returns_questions_with_answers(self, monkeypatch):
        _patch_db(monkeypatch, {"quiz_questions": [_question()]})
        out = asyncio.run(get_quiz("t", BackgroundTasks(), caller_id="u"))
        assert len(out) == 1 and out[0]["correct_index"] == 0 and out[0]["explanation"] == "why"

    def test_empty_when_none(self, monkeypatch):
        _patch_db(monkeypatch, {"quiz_questions": []})
        assert asyncio.run(get_quiz("t", BackgroundTasks(), caller_id="u")) == []

    def test_db_failure_returns_empty(self, monkeypatch):
        _patch_db(monkeypatch, {"quiz_questions": [_question()]}, fail={"quiz_questions"})
        assert asyncio.run(get_quiz("t", BackgroundTasks(), caller_id="u")) == []


class TestSelfHeal:
    def setup_method(self):
        quiz_api._quiz_generating.clear()

    def test_schedules_generation_when_empty_and_has_clips(self, monkeypatch):
        _patch_db(monkeypatch, {"quiz_questions": [], "clips": [{"id": "c1", "topic_slug": "t"}]})
        bg = BackgroundTasks()
        out = asyncio.run(get_quiz("t", bg, caller_id="u"))
        assert out == []
        assert len(bg.tasks) == 1
        assert "t" in quiz_api._quiz_generating

    def test_no_generation_when_no_clips(self, monkeypatch):
        _patch_db(monkeypatch, {"quiz_questions": [], "clips": []})
        bg = BackgroundTasks()
        asyncio.run(get_quiz("t", bg, caller_id="u"))
        assert bg.tasks == []
        assert "t" not in quiz_api._quiz_generating

    def test_no_generation_when_questions_exist(self, monkeypatch):
        _patch_db(monkeypatch, {"quiz_questions": [_question()], "clips": [{"id": "c1", "topic_slug": "t"}]})
        bg = BackgroundTasks()
        asyncio.run(get_quiz("t", bg, caller_id="u"))
        assert bg.tasks == []

    def test_no_duplicate_generation_when_in_flight(self, monkeypatch):
        _patch_db(monkeypatch, {"quiz_questions": [], "clips": [{"id": "c1", "topic_slug": "t"}]})
        quiz_api._quiz_generating.add("t")
        bg = BackgroundTasks()
        asyncio.run(get_quiz("t", bg, caller_id="u"))
        assert bg.tasks == []


class TestAnswerQuestion:
    def test_records_correct_with_points(self, monkeypatch):
        db = _patch_db(monkeypatch, {"quiz_questions": [_question(correct_index=1)], "quiz_results": []})
        asyncio.run(answer_question("q1", QuizAnswer(chosen_index=1, session_id="s", streak=0), caller_id="u"))
        rows = [p for tbl, p in db.rec["inserts"] if tbl == "quiz_results"]
        assert len(rows) == 1
        assert rows[0]["correct"] is True and rows[0]["points"] == points_for(0)
        assert rows[0]["user_id"] == "u" and rows[0]["topic_slug"] == "t"

    def test_incorrect_records_zero_points(self, monkeypatch):
        db = _patch_db(monkeypatch, {"quiz_questions": [_question(correct_index=1)], "quiz_results": []})
        asyncio.run(answer_question("q1", QuizAnswer(chosen_index=0), caller_id="u"))
        row = [p for tbl, p in db.rec["inserts"] if tbl == "quiz_results"][0]
        assert row["correct"] is False and row["points"] == 0

    def test_points_awarded_once(self, monkeypatch):
        # a prior correct answer by this user exists -> second correct awards 0
        db = _patch_db(monkeypatch, {
            "quiz_questions": [_question(correct_index=1)],
            "quiz_results": [{"id": "r0", "question_id": "q1", "user_id": "u", "correct": True}],
        })
        asyncio.run(answer_question("q1", QuizAnswer(chosen_index=1, streak=3), caller_id="u"))
        new = [p for tbl, p in db.rec["inserts"] if tbl == "quiz_results"][0]
        assert new["correct"] is True and new["points"] == 0

    def test_streak_increases_points(self, monkeypatch):
        db = _patch_db(monkeypatch, {"quiz_questions": [_question(correct_index=0)], "quiz_results": []})
        asyncio.run(answer_question("q1", QuizAnswer(chosen_index=0, streak=2), caller_id="u"))
        row = [p for tbl, p in db.rec["inserts"] if tbl == "quiz_results"][0]
        assert row["points"] == points_for(2) > points_for(0)

    def test_unknown_question_records_nothing(self, monkeypatch):
        db = _patch_db(monkeypatch, {"quiz_questions": [], "quiz_results": []})
        asyncio.run(answer_question("ghost", QuizAnswer(chosen_index=0), caller_id="u"))
        assert db.rec["inserts"] == []

    def test_db_failure_never_raises(self, monkeypatch):
        _patch_db(monkeypatch, {"quiz_questions": [_question()], "quiz_results": []}, fail={"quiz_results"})
        # must not raise even though the prior-check + insert both hit the failing table
        asyncio.run(answer_question("q1", QuizAnswer(chosen_index=0), caller_id="u"))


class TestGetMastery:
    def test_summary_for_caller(self, monkeypatch):
        _patch_db(monkeypatch, {"quiz_results": [
            {"topic_slug": "t", "user_id": "u", "correct": True, "points": 10},
            {"topic_slug": "t", "user_id": "u", "correct": False, "points": 0},
        ]})
        out = asyncio.run(get_mastery("u", caller_id="u"))
        assert out["total_points"] == 10
        assert out["topics"]["t"]["correct_rate"] == 0.5

    def test_forbidden_for_other_user(self, monkeypatch):
        _patch_db(monkeypatch, {"quiz_results": []})
        with pytest.raises(HTTPException) as e:
            asyncio.run(get_mastery("someone-else", caller_id="u"))
        assert e.value.status_code == 403

    def test_db_failure_returns_empty_summary(self, monkeypatch):
        _patch_db(monkeypatch, {"quiz_results": []}, fail={"quiz_results"})
        out = asyncio.run(get_mastery("u", caller_id="u"))
        assert out == {"topics": {}, "total_points": 0}
