"""Example test for ``generate_probe`` shape (mocked LLM client).

Asserts that ``generate_probe(topic)`` returns exactly 6 multiple-choice
questions, each carrying a ``concept_tag`` and a ``level``, when the Game_Model
returns a markdown-fenced JSON array of 6 valid MCQs. The LLM client is mocked
so no real call is made; this exercises the Fence_Stripping_Parse pattern
(``quiz._strip_json`` → ``json.loads``) and the reused ``quiz._validate_question``
filter that ``generate_probe`` runs every candidate through.

Validates: Requirements 2.1, 2.3
"""
import json
from unittest.mock import MagicMock, patch

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


def _fenced_probe_payload() -> str:
    """A 6-question probe with a ~2 prereq / 2 core / 2 stretch gradient,
    wrapped in a markdown ```json code fence so the fence-stripping parse runs."""
    questions = [
        _probe_question(0, "prerequisite"),
        _probe_question(1, "prerequisite"),
        _probe_question(2, "core"),
        _probe_question(3, "core"),
        _probe_question(4, "stretch"),
        _probe_question(5, "stretch"),
    ]
    return "```json\n" + json.dumps(questions) + "\n```"


def _mock_client(content: str) -> MagicMock:
    """A mock OpenAI client whose chat.completions.create returns ``content``."""
    client = MagicMock()
    client.chat.completions.create.return_value = _FakeResponse(content)
    return client


class TestGenerateProbeShape:
    def test_returns_six_questions_each_with_concept_tag_and_level(self):
        client = _mock_client(_fenced_probe_payload())

        with patch.object(game.llm, "get_client", return_value=client):
            probe = game.generate_probe("backtracking")

        # Exactly 6 questions parsed via Fence_Stripping_Parse (Req 2.1).
        assert len(probe) == 6

        # Each question carries a concept_tag and a level (Req 2.3).
        for question in probe:
            assert question.get("concept_tag")
            assert question.get("level") in {"prerequisite", "core", "stretch"}

        # The difficulty gradient survives: ~2 prerequisite / 2 core / 2 stretch.
        levels = [q["level"] for q in probe]
        assert levels.count("prerequisite") == 2
        assert levels.count("core") == 2
        assert levels.count("stretch") == 2

    def test_strips_code_fence_before_parsing(self):
        # The payload is wrapped in a ```json fence; generate_probe must strip it
        # and still parse 6 questions rather than choking on the fence.
        client = _mock_client(_fenced_probe_payload())

        with patch.object(game.llm, "get_client", return_value=client):
            probe = game.generate_probe("the French Revolution")

        assert len(probe) == 6
        client.chat.completions.create.assert_called_once()
