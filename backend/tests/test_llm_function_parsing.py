"""Unit tests for LLM-function parsing and the fallback boundary (mocked client).

Covers the Fence_Stripping_Parse pattern (``quiz._strip_json`` → ``json.loads``)
for the three raw decision LLM functions ``decide_next`` / ``intuition`` /
``clip_query`` when the Game_Model returns markdown-fenced JSON, and pins down
exactly *where* the single-retry-then-safe-fallback path lives.

NOTE on the fallback (Req 2.4, 5.8, 9.3): the Error Handling table in design.md
specifies a "log and retry once; on repeat, return a safe fallback (REINFORCE on
the current node with a generic diagnosis)" path for ``decide_next`` /
``intuition`` / ``clip_query``. As currently implemented (tasks 2.3/2.5), these
are the RAW LLM functions — they parse the strict-JSON response with
Fence_Stripping_Parse and RAISE on unparseable input so a caller can decide how
to recover. The retry-then-safe-fallback wrapper is realized in the ``decide``
orchestrator (task 3.3), not yet implemented; the fallback behavior is therefore
asserted in the ``decide()`` tests (tasks 3.4–3.7). Here we (a) verify the
Fence_Stripping_Parse of fenced output for all three functions, and (b) pin the
raise-on-unparseable contract that the orchestrator's single-retry-then-fallback
will build on.

Validates: Requirements 2.4, 5.8, 9.3
"""
import json

import pytest
from unittest.mock import MagicMock, patch

from app.services import game


# ---------------------------------------------------------------------------
# Fake OpenAI client — shapes match resp.choices[0].message.content access.
# (Mirrors the doubles in test_generate_probe.py.)
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


def _mock_client(content: str) -> MagicMock:
    """A mock OpenAI client whose chat.completions.create returns ``content``."""
    client = MagicMock()
    client.chat.completions.create.return_value = _FakeResponse(content)
    return client


def _fence(payload) -> str:
    """Wrap a JSON-serializable payload in a markdown ```json code fence so the
    Fence_Stripping_Parse pattern has a fence to strip."""
    return "```json\n" + json.dumps(payload) + "\n```"


# ---------------------------------------------------------------------------
# (a) Fence_Stripping_Parse of fenced JSON for the three decision functions
# ---------------------------------------------------------------------------

class TestDecideNextParsing:
    """``decide_next`` strips the fence and returns the raw decision dict
    ``{action, next_node, reached_goal, gap, diagnosis}`` (Req 5.2, 5.8)."""

    def test_strips_fence_and_returns_decision_dict(self):
        decision = {
            "action": "DESCEND",
            "next_node": "recursion",
            "reached_goal": False,
            "gap": "recursion",
            "diagnosis": "You stumble where the recursive call unwinds.",
        }
        client = _mock_client(_fence(decision))

        with patch.object(game.llm, "get_client", return_value=client):
            result = game.decide_next(
                goal="backtracking",
                current_node="depth-first search",
                path=["backtracking"],
                score_pct=0.1667,
                band="DESCEND",
                missed_concepts=["recursion"],
                correct_concepts=[],
            )

        assert result == decision
        # The band is passed IN; the LLM is never asked to choose it (Req 5.5).
        client.chat.completions.create.assert_called_once()
        sent_prompt = client.chat.completions.create.call_args.kwargs["messages"][0][
            "content"
        ]
        assert "BAND = DESCEND" in sent_prompt

    def test_parses_plain_unfenced_json(self):
        # Fence_Stripping_Parse is a no-op when there is no fence: plain strict
        # JSON must parse just as well as fenced JSON.
        decision = {
            "action": "CLIMB",
            "next_node": None,
            "reached_goal": True,
            "gap": "backtracking",
            "diagnosis": "You've got it — this was the goal.",
        }
        client = _mock_client(json.dumps(decision))

        with patch.object(game.llm, "get_client", return_value=client):
            result = game.decide_next(
                goal="backtracking",
                current_node="backtracking",
                path=["backtracking"],
                score_pct=0.85,
                band="CLIMB",
                missed_concepts=[],
                correct_concepts=["backtracking"],
            )

        assert result["reached_goal"] is True
        assert result["next_node"] is None


class TestIntuitionParsing:
    """``intuition`` strips the fence and returns ``{hook}`` (Req 9.1, 9.3)."""

    def test_strips_fence_and_returns_hook(self):
        payload = {"hook": "You can think of recursion as a stack of mirrors."}
        client = _mock_client(_fence(payload))

        with patch.object(game.llm, "get_client", return_value=client):
            result = game.intuition("recursion", "backtracking")

        assert result == payload
        assert result["hook"].startswith("You")
        # Issued with the Game_Model at the intuition temperature (Req 9.2).
        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == game.MODEL
        assert kwargs["temperature"] == game.INTUITION_TEMPERATURE


class TestClipQueryParsing:
    """``clip_query`` strips the fence and returns ``{query}`` (Req 10.1)."""

    def test_strips_fence_and_returns_query(self):
        payload = {"query": "recursion explained simply for beginners"}
        client = _mock_client(_fence(payload))

        with patch.object(game.llm, "get_client", return_value=client):
            result = game.clip_query("recursion", "backtracking")

        assert result == payload
        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == game.MODEL
        assert kwargs["temperature"] == game.CLIP_QUERY_TEMPERATURE


# ---------------------------------------------------------------------------
# (b) Raise-on-unparseable contract — the foundation the orchestrator's
#     single-retry-then-safe-fallback (task 3.3 ``decide``) builds on.
#
# The safe-fallback path itself (retry once, then REINFORCE-on-current-node with
# a generic diagnosis) is realized in ``decide`` and asserted in its tests; at
# the raw-function level the only behavior that exists is: parse, and raise so
# the caller can recover.
# ---------------------------------------------------------------------------

class TestRawFunctionsRaiseOnUnparseable:
    @pytest.mark.parametrize(
        "fn, args",
        [
            (
                "decide_next",
                dict(
                    goal="g",
                    current_node="n",
                    path=[],
                    score_pct=0.5,
                    band="REINFORCE",
                    missed_concepts=[],
                    correct_concepts=[],
                ),
            ),
            ("intuition", dict(node="n", goal="g")),
            ("clip_query", dict(node="n", goal="g")),
        ],
    )
    def test_unparseable_response_raises(self, fn, args):
        # Non-JSON / fenced garbage: Fence_Stripping_Parse strips the fence and
        # json.loads raises (ValueError subclass). The raw function surfaces it
        # rather than masking it — the retry/fallback is the orchestrator's job.
        client = _mock_client("```json\nnot valid json at all {{{\n```")

        with patch.object(game.llm, "get_client", return_value=client):
            with pytest.raises(ValueError):
                getattr(game, fn)(**args)
