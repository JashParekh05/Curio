"""Integration test for a NON-CS topic: "the French Revolution" (Task 5.6).

This is the cross-domain twin of the CS-topic integration test. It proves the
Game_Service is fully generative for a history topic — there is NO hardcoded
curriculum and NO knowledge graph; the prerequisite chain, intuition text, clip
query, and every quiz are produced entirely from LLM calls (Req 12.1) — and that
a non-CS topic completes a full descend -> reinforce -> climb loop end-to-end
(Req 12.3), exactly as a CS topic does.

The whole loop is driven through the REAL service orchestration
(``start_session`` -> ``decide`` -> ``deliver_node``) with only the leaf I/O
stubbed via ``unittest.mock``:

* the **LLM client** (``game.llm.get_client`` and ``quiz._client``, which the
  reused quiz judge uses) is replaced by a single ``ScriptedLLM`` whose
  ``chat.completions.create`` inspects the prompt and returns scripted strict
  JSON for each of the five game LLM functions plus the quiz quality judge; and
* the **youtube functions** (``youtube.youtube_search`` /
  ``youtube._fetch_transcript``) return scripted clips and a transcript.

Because the LLM *client* is stubbed (not the game functions), the test exercises
the real prompt building, the Fence_Stripping_Parse pattern, ``_validate_question``
filtering, the deterministic banding/grading, the descent/climb safety
enforcement, clip selection, and the transcript-grounded quiz path — i.e. the
whole service, end to end.

The session is stateless on the backend, so it is driven like the real client
would: ``floor_node`` / ``descent_depth`` / ``path`` / ``current_node`` are
threaded between successive ``decide`` calls, and the band of each step is forced
deterministically through the constructed answers (all-wrong -> DESCEND, two of
three -> REINFORCE, all-correct -> CLIMB) so the code-computed band is exactly
the one under test.

The scripted journey for "the French Revolution":

  probe(goal)            --DESCEND-->  "absolute monarchy in France"
  node quiz              --DESCEND-->  "what a social hierarchy is"   (descending)
  node re-quiz           --REINFORCE-> "what a social hierarchy is"   (edge)
  node quiz              --CLIMB---->  "causes of the French Revolution" (floor set)
  node quiz              --CLIMB---->  "the French Revolution"        (climb to goal)
  node quiz on the goal  --CLIMB---->  reached_goal == True

Run from the backend/ dir:
``.venv/bin/python -m pytest tests/test_game_integration_non_cs.py``.

Validates: Requirements 12.1, 12.3
"""
import json
import math
import re
from unittest.mock import patch

import pytest

from app.services import game, quiz, youtube
from app.services.game import DecideRequest


GOAL = "the French Revolution"


# ---------------------------------------------------------------------------
# Scripted LLM client.
#
# A single fake client routes ``chat.completions.create`` to the right scripted
# strict-JSON payload by inspecting the prompt. The five game LLM functions and
# the reused quiz judge each have a distinct, stable phrase in their prompt.
# ---------------------------------------------------------------------------

class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Completion:
    def __init__(self, content):
        self.choices = [_Choice(content)]


def _probe_payload() -> str:
    """Six valid MCQs aimed AT the topic with a 2 prereq / 2 core / 2 stretch
    gradient, each carrying a concept_tag and a level (Req 2.1, 2.3). Every
    question has correct_index 0 so the test can pin the graded band."""
    levels = [
        ("prerequisite", "what a monarchy is"),
        ("prerequisite", "what a social hierarchy is"),
        ("core", "causes of the French Revolution"),
        ("core", "the storming of the Bastille"),
        ("stretch", "the Reign of Terror"),
        ("stretch", "Napoleon's rise from the revolution"),
    ]
    questions = []
    for i, (level, tag) in enumerate(levels):
        questions.append(
            {
                "question": f"Placement question {i} about {tag}?",
                "options": [f"Correct {i}", f"Misconception {i}", f"Distractor {i}"],
                "correct_index": 0,
                "explanation": f"Option 0 is the right read of {tag}.",
                "concept_tag": tag,
                "level": level,
            }
        )
    return json.dumps(questions)


def _quiz_payload(node: str) -> str:
    """Three valid checkpoint MCQs for a node, each with a concept_tag and
    correct_index 0 (Req 11.3)."""
    questions = [
        {
            "question": f"Checkpoint {i} on understanding {node}?",
            "options": [f"Right {i}", f"Plausible-but-wrong {i}", f"Trap {i}"],
            "correct_index": 0,
            "explanation": f"Option 0 reflects {node} correctly.",
            "concept_tag": f"{node} :: sub-concept {i}",
        }
        for i in range(3)
    ]
    return json.dumps(questions)


class ScriptedLLM:
    """Fake OpenAI client returning scripted strict JSON per prompt type.

    ``decide_decisions`` is a queue of raw ``decide_next`` decision dicts popped
    in order, one per ``decide_next`` call — the "recorded" LLM next-step
    responses that drive the scripted descend/reinforce/climb journey. All other
    prompt types are answered statelessly. Call counts per function are recorded
    so the test can assert every artifact was generated (no hardcoded
    curriculum, Req 12.1).
    """

    def __init__(self, decide_decisions):
        self._decide_decisions = list(decide_decisions)
        self.calls = {
            "probe": 0,
            "decide": 0,
            "quiz": 0,
            "intuition": 0,
            "clip_query": 0,
            "judge": 0,
        }
        # Expose the OpenAI-ish surface: client.chat.completions.create(...).
        self.chat = self

    # client.chat.completions -> self; self.completions.create -> self.create
    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        prompt = kwargs["messages"][0]["content"]

        if "PLACEMENT DIAGNOSTIC" in prompt:
            self.calls["probe"] += 1
            return _Completion(_probe_payload())

        if "adaptive engine of a learning game" in prompt:
            self.calls["decide"] += 1
            decision = self._decide_decisions.pop(0)
            return _Completion(json.dumps(decision))

        if "strict quiz reviewer" in prompt:
            # The reused quiz quality judge — accept every well-formed MCQ.
            self.calls["judge"] += 1
            return _Completion(json.dumps({"ok": True, "issue": ""}))

        if "CHECKPOINT quiz" in prompt:
            self.calls["quiz"] += 1
            node = _extract(prompt, r'concept "([^"]+)"') or "the concept"
            return _Completion(_quiz_payload(node))

        if "core INTUITION" in prompt:
            self.calls["intuition"] += 1
            node = _extract(prompt, r'concept "([^"]+)"') or "this idea"
            return _Completion(
                json.dumps(
                    {"hook": f"You can picture {node} as one vivid, concrete story."}
                )
            )

        if "YouTube SEARCH QUERY" in prompt:
            self.calls["clip_query"] += 1
            node = _extract(prompt, r'concept "([^"]+)"') or "the topic"
            return _Completion(json.dumps({"query": f"{node} explained short"}))

        raise AssertionError(f"unexpected prompt routed to ScriptedLLM:\n{prompt[:200]}")


def _extract(text: str, pattern: str) -> str | None:
    m = re.search(pattern, text)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Scripted youtube leaf I/O.
# ---------------------------------------------------------------------------

# A mixed candidate set: two clips in the ideal 60-180s range (one captioned,
# one not) plus a long high-view clip. select_clip must pick the captioned
# in-range clip over the uncaptioned in-range clip and over the long clip
# (Req 10.5), so the chosen video_id is deterministic. All mention the goal
# ("French Revolution") so they survive the on-topic relevance filter.
_SCRIPTED_CLIPS = [
    {
        "video_id": "vid_inrange_nocap",
        "title": "The French Revolution, short (no captions)",
        "channel_title": "History Quick",
        "duration_seconds": 120,
        "has_caption": False,
        "view_count": 5000,
        "thumbnail_url": "https://img/1",
        "description": "A short explainer on the French Revolution.",
    },
    {
        "video_id": "vid_inrange_cap",
        "title": "The French Revolution, short (captioned)",
        "channel_title": "History Quick",
        "duration_seconds": 150,
        "has_caption": True,
        "view_count": 800,
        "thumbnail_url": "https://img/2",
        "description": "A short captioned explainer on the French Revolution.",
    },
    {
        "video_id": "vid_long_popular",
        "title": "The French Revolution, long lecture",
        "channel_title": "Deep Dive",
        "duration_seconds": 480,
        "has_caption": True,
        "view_count": 999999,
        "thumbnail_url": "https://img/3",
        "description": "A long lecture.",
    },
]
_EXPECTED_CLIP_ID = "vid_inrange_cap"

_SCRIPTED_TRANSCRIPT = [
    {"start": 0.0, "duration": 4.0, "text": "Here is the core idea explained simply."},
    {"start": 4.0, "duration": 4.0, "text": "And here is how it connects to the goal."},
]


# ---------------------------------------------------------------------------
# Band-driving answer helper.
# ---------------------------------------------------------------------------

def _answers_for_band(questions: list[dict], target: str) -> list[int]:
    """Build answers whose graded score lands in the target band's range.

    Every scripted question has ``correct_index`` 0, so answering 0 is correct
    and any other index is wrong. ``grade_quiz`` -> ``band`` then maps the score
    fraction deterministically: all-wrong (0.0) -> DESCEND, all-correct
    (1.0) -> CLIMB, and a fraction in [0.40, 0.70] -> REINFORCE.
    """
    n = len(questions)
    correct = [int(q["correct_index"]) for q in questions]
    wrong = [(int(q["correct_index"]) + 1) % len(q["options"]) for q in questions]

    if target == "DESCEND":
        return list(wrong)
    if target == "CLIMB":
        return list(correct)
    # REINFORCE: answer k correct so that 0.40 <= k/n <= 0.70.
    lo = math.ceil(0.40 * n)
    hi = math.floor(0.70 * n)
    k = max(lo, min(hi, lo))
    answers = list(correct)
    for i in range(k, n):
        answers[i] = wrong[i]
    return answers


# ---------------------------------------------------------------------------
# The scripted decide_next journey (the "recorded" LLM next-step responses).
# ---------------------------------------------------------------------------

def _decision(action: str, next_node, *, reached_goal: bool = False) -> dict:
    return {
        "action": action,
        "next_node": next_node,
        "reached_goal": reached_goal,
        "gap": f"the concept '{next_node}' targets",
        "diagnosis": "You are close; this next step shores up the gap.",
    }


# Popped one per decide_next call, in journey order. The action matches the
# band the answers force, so decide() treats each as a genuine step rather than
# coercing it. The final decision is irrelevant (decide short-circuits on the
# goal) but is supplied so the queue never underflows.
_DECIDE_JOURNEY = [
    _decision("DESCEND", "absolute monarchy in France"),       # probe: descend
    _decision("DESCEND", "what a social hierarchy is"),         # descend to floor
    _decision("REINFORCE", "what a social hierarchy is"),       # reinforce at edge
    _decision("CLIMB", "causes of the French Revolution"),      # climb out (floor set)
    _decision("CLIMB", GOAL),                                   # climb to the goal
    _decision("CLIMB", None, reached_goal=True),                # climb on the goal
]


class TestFrenchRevolutionFullLoop:
    """Drive the entire descend -> reinforce -> climb loop for a non-CS topic."""

    def test_full_descend_reinforce_climb_loop_reaches_goal(self):
        client = ScriptedLLM(_DECIDE_JOURNEY)

        with patch.object(game.llm, "get_client", return_value=client), patch.object(
            quiz, "_client", return_value=client
        ), patch.object(
            youtube, "youtube_search", return_value=list(_SCRIPTED_CLIPS)
        ) as search, patch.object(
            youtube, "_fetch_transcript", return_value=list(_SCRIPTED_TRANSCRIPT)
        ), patch.object(
            quiz, "generate_and_store_questions"
        ) as store_orchestrator:

            # --- Start the session: goal entry + placement probe (Req 1, 2) ---
            session = game.start_session(GOAL)
            assert session.goal == GOAL
            assert session.current_node == GOAL  # placement aimed AT the goal
            assert len(session.probe) == 6
            for q in session.probe:
                assert q["concept_tag"]
                assert q["level"] in {"prerequisite", "core", "stretch"}

            # Stateless backend: the client threads the session state.
            current_node = session.current_node
            path: list[str] = []
            floor_node: str | None = None
            descent_depth = 0

            def run_decide(questions, target_band):
                nonlocal current_node, path, floor_node, descent_depth
                answers = _answers_for_band(questions, target_band)
                req = DecideRequest(
                    goal=GOAL,
                    current_node=current_node,
                    path=list(path),
                    questions=questions,
                    answers=answers,
                    floor_node=floor_node,
                    descent_depth=descent_depth,
                )
                resp = game.decide(req)
                # Thread the returned state forward, mirroring the real client.
                floor_node = resp.floor_node
                descent_depth = resp.descent_depth
                if resp.next_node is not None and (
                    game._norm_node(resp.next_node) != game._norm_node(current_node)
                ):
                    current_node = resp.next_node
                    path.append(current_node)
                return resp

            def deliver(node):
                payload = game.deliver_node(node, GOAL)
                # Every node beat is fully generated: a hook, a selected clip
                # exposed as a canonical embed URL, and exactly 3 tagged MCQs.
                assert payload.hook
                assert payload.clip is not None
                assert payload.clip["video_id"] == _EXPECTED_CLIP_ID
                assert (
                    payload.clip["video_url"]
                    == f"https://www.youtube.com/embed/{_EXPECTED_CLIP_ID}"
                )
                assert len(payload.quiz) == 3
                for q in payload.quiz:
                    assert q["concept_tag"]
                return payload

            # --- Step 1: probe graded DESCEND -> descend off the goal ----------
            resp1 = run_decide(session.probe, "DESCEND")
            assert resp1.band == "DESCEND"
            assert resp1.action == "DESCEND"
            assert resp1.next_node == "absolute monarchy in France"
            assert resp1.floor_node is None  # genuine descent: no floor yet
            assert resp1.descent_depth == 1

            payload1 = deliver(current_node)

            # --- Step 2: node quiz DESCEND again -> deeper prerequisite --------
            resp2 = run_decide(payload1.quiz, "DESCEND")
            assert resp2.band == "DESCEND"
            assert resp2.action == "DESCEND"
            assert resp2.next_node == "what a social hierarchy is"
            assert resp2.descent_depth == 2
            assert resp2.floor_node is None

            payload2 = deliver(current_node)

            # --- Step 3: REINFORCE at the edge -> stay on the same node --------
            node_before_reinforce = current_node
            resp3 = run_decide(payload2.quiz, "REINFORCE")
            assert resp3.band == "REINFORCE"
            assert resp3.action == "REINFORCE"
            assert resp3.next_node == node_before_reinforce
            assert current_node == node_before_reinforce  # did not advance

            # The reinforce delivers a FRESH re-quiz on the SAME node (Req 7.3).
            payload3 = deliver(current_node)
            assert payload3.node == node_before_reinforce

            # --- Step 4: CLIMB out of the descent -> floor marked once ---------
            climbed_out_of = current_node
            resp4 = run_decide(payload3.quiz, "CLIMB")
            assert resp4.band == "CLIMB"
            assert resp4.action == "CLIMB"
            assert resp4.reached_goal is False
            assert resp4.next_node == "causes of the French Revolution"
            # The first climb out of a descent marks the Floor_Node (Req 6.2).
            assert resp4.floor_node == climbed_out_of

            payload4 = deliver(current_node)

            # --- Step 5: CLIMB one more step -> arrive AT the goal -------------
            resp5 = run_decide(payload4.quiz, "CLIMB")
            assert resp5.band == "CLIMB"
            assert resp5.action == "CLIMB"
            assert resp5.reached_goal is False
            assert resp5.next_node == GOAL
            assert current_node == GOAL
            # The floor is set exactly once: the later climb does not move it.
            assert resp5.floor_node == climbed_out_of

            payload5 = deliver(current_node)

            # --- Step 6: CLIMB on the goal -> the session goal is reached ------
            resp6 = run_decide(payload5.quiz, "CLIMB")
            assert resp6.band == "CLIMB"
            assert resp6.reached_goal is True  # Req 8.2 / 12.3 — goal reached
            assert resp6.next_node is None      # never advances past the goal
            assert resp6.floor_node == climbed_out_of

        # --- Cross-cutting assertions over the whole loop ----------------------

        # All three outcomes were exercised across the loop (Req 12.3).
        bands = [resp1.band, resp2.band, resp3.band, resp4.band, resp5.band, resp6.band]
        assert "DESCEND" in bands
        assert "REINFORCE" in bands
        assert "CLIMB" in bands

        # Every artifact was produced from LLM calls — no hardcoded curriculum
        # or knowledge graph anywhere in the path (Req 12.1).
        assert client.calls["probe"] == 1          # the placement probe
        assert client.calls["decide"] == 6         # one decision per step
        assert client.calls["intuition"] == 5      # one hook per delivered node
        assert client.calls["clip_query"] == 5     # one query per delivered node
        assert client.calls["quiz"] == 5           # one checkpoint quiz per node
        assert client.calls["judge"] >= 5          # the reused quality judge ran
        # The clip search was driven by the generated query for every node.
        assert search.call_count == 5

        # The DB-coupled orchestrator is NEVER used for on-the-fly node quizzes
        # (Req 11.5) — the generative path stays decoupled from storage.
        store_orchestrator.assert_not_called()

    def test_generative_path_uses_no_hardcoded_curriculum(self):
        # A tighter restatement of Req 12.1 for a non-CS topic: the descent
        # chain is whatever the (scripted) LLM proposes, not any predefined
        # list. Swapping the scripted next-nodes changes the realized path,
        # proving the chain is generative rather than hardcoded.
        alt_journey = [
            _decision("DESCEND", "the idea of divine right"),
            _decision("REINFORCE", "the idea of divine right"),
            _decision("CLIMB", GOAL),
            _decision("CLIMB", None, reached_goal=True),
        ]
        client = ScriptedLLM(alt_journey)

        with patch.object(game.llm, "get_client", return_value=client), patch.object(
            quiz, "_client", return_value=client
        ), patch.object(
            youtube, "youtube_search", return_value=list(_SCRIPTED_CLIPS)
        ), patch.object(
            youtube, "_fetch_transcript", return_value=list(_SCRIPTED_TRANSCRIPT)
        ):
            session = game.start_session(GOAL)
            current_node = session.current_node
            path: list[str] = []
            floor_node: str | None = None
            descent_depth = 0

            realized_path = []

            def run_decide(questions, target_band):
                nonlocal current_node, path, floor_node, descent_depth
                answers = _answers_for_band(questions, target_band)
                resp = game.decide(
                    DecideRequest(
                        goal=GOAL,
                        current_node=current_node,
                        path=list(path),
                        questions=questions,
                        answers=answers,
                        floor_node=floor_node,
                        descent_depth=descent_depth,
                    )
                )
                floor_node = resp.floor_node
                descent_depth = resp.descent_depth
                if resp.next_node is not None and (
                    game._norm_node(resp.next_node) != game._norm_node(current_node)
                ):
                    current_node = resp.next_node
                    path.append(current_node)
                    realized_path.append(current_node)
                return resp

            r1 = run_decide(session.probe, "DESCEND")
            p1 = game.deliver_node(current_node, GOAL)
            r2 = run_decide(p1.quiz, "REINFORCE")
            p2 = game.deliver_node(current_node, GOAL)
            r3 = run_decide(p2.quiz, "CLIMB")
            p3 = game.deliver_node(current_node, GOAL)
            r4 = run_decide(p3.quiz, "CLIMB")

            assert r1.action == "DESCEND"
            assert r2.action == "REINFORCE"
            assert r3.action == "CLIMB"
            assert r4.reached_goal is True
            # The realized prerequisite/climb chain is the one the LLM scripted,
            # confirming the path is generated, not drawn from a fixed catalog.
            assert "the idea of divine right" in realized_path
            assert realized_path[-1] == GOAL
