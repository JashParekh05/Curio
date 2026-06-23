"""Example/edge test for ``start_session`` (mocked LLM client).

Two cases, both driving the real ``generate_probe`` → ``start_session`` path
with the OpenAI client mocked (no real call is made):

(a) The model returns 6 valid MCQs → ``start_session`` treats the topic as the
    Goal_Node (``goal == topic``, ``current_node == goal``) and returns a
    SessionStart carrying exactly 6 probe questions (Req 1.1, 2.1).

(b) The model returns a short/malformed probe (e.g. only 3 questions, or
    unparseable garbage) → ``start_session`` raises ``ProbeGenerationError``
    rather than returning a partial probe (Req 2.5).

Validates: Requirements 1.1, 2.5
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from app.services import game


# ---------------------------------------------------------------------------
# Fake OpenAI client — shapes match resp.choices[0].message.content access.
# ---------------------------------------------------------------------------

class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


def _probe_question(i: int, level: str) -> dict:
    """A valid MCQ carrying a concept_tag and a level (passes _validate_question)."""
    return {
        "question": f"Which statement about concept {i} is correct?",
        "options": [f"Right answer {i}", f"Plausible miss {i}", f"Another miss {i}"],
        "correct_index": 0,
        "explanation": f"Option 0 is correct because of reason {i}.",
        "concept_tag": f"concept-{i}",
        "level": level,
    }


def _fenced(questions: list[dict]) -> str:
    """Wrap a question list in a ```json code fence (exercises fence-stripping)."""
    return "```json\n" + json.dumps(questions) + "\n```"


def _six_valid_questions() -> list[dict]:
    """A full 6-question probe with the ~2 prereq / 2 core / 2 stretch gradient."""
    return [
        _probe_question(0, "prerequisite"),
        _probe_question(1, "prerequisite"),
        _probe_question(2, "core"),
        _probe_question(3, "core"),
        _probe_question(4, "stretch"),
        _probe_question(5, "stretch"),
    ]


def _mock_client(content: str) -> MagicMock:
    """A mock OpenAI client whose chat.completions.create returns ``content``."""
    client = MagicMock()
    client.chat.completions.create.return_value = _FakeResponse(content)
    return client


class TestStartSessionGoalFromTopic:
    def test_goal_and_current_node_set_from_topic_with_six_questions(self):
        # (a) 6 valid MCQs → goal == topic, current_node == goal, 6 questions.
        client = _mock_client(_fenced(_six_valid_questions()))

        with patch.object(game.llm, "get_client", return_value=client):
            session = game.start_session("backtracking")

        # The non-empty topic becomes the Goal_Node and placement starts there
        # (current_node == goal), not at lesson one (Req 1.1).
        assert session.goal == "backtracking"
        assert session.current_node == session.goal

        # Exactly 6 probe questions are returned (Req 2.1), each carrying the
        # concept_tag + level that survive validation.
        assert len(session.probe) == 6
        for question in session.probe:
            assert question.get("concept_tag")
            assert question.get("level") in {"prerequisite", "core", "stretch"}

        # A fresh session id was minted.
        assert session.session_id

    def test_topic_is_trimmed_into_goal(self):
        # Surrounding whitespace is stripped so the Goal_Node is the bare topic.
        client = _mock_client(_fenced(_six_valid_questions()))

        with patch.object(game.llm, "get_client", return_value=client):
            session = game.start_session("  the French Revolution  ")

        assert session.goal == "the French Revolution"
        assert session.current_node == "the French Revolution"


class TestStartSessionShortOrMalformedProbe:
    def test_short_probe_raises_rather_than_partial_quiz(self):
        # (b) Only 3 valid questions come back: short of the required 6, so
        # start_session surfaces an error instead of a partial probe (Req 2.5).
        short = _six_valid_questions()[:3]
        client = _mock_client(_fenced(short))

        with patch.object(game.llm, "get_client", return_value=client):
            with pytest.raises(game.ProbeGenerationError):
                game.start_session("backtracking")

    def test_malformed_probe_raises_rather_than_partial_quiz(self):
        # Unparseable garbage (not JSON) → ProbeGenerationError, no partial probe.
        client = _mock_client("not json at all — sorry!")

        with patch.object(game.llm, "get_client", return_value=client):
            with pytest.raises(game.ProbeGenerationError):
                game.start_session("the French Revolution")

    def test_invalid_questions_are_filtered_then_shortfall_raises(self):
        # A mix where most candidates fail _validate_question leaves fewer than 6
        # valid MCQs — the shortfall must raise rather than return what survived.
        malformed = {"question": "", "options": [], "correct_index": 9}
        candidates = _six_valid_questions()[:2] + [malformed, malformed, malformed, malformed]
        client = _mock_client(_fenced(candidates))

        with patch.object(game.llm, "get_client", return_value=client):
            with pytest.raises(game.ProbeGenerationError):
                game.start_session("backtracking")
