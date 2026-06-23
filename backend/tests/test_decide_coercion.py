"""Edge/example tests for ``decide`` coercion and failure handling.

These drive the real ``decide`` orchestrator with the raw ``decide_next`` LLM
function stubbed via ``unittest.mock.patch`` (no real call is made). The quiz
``questions``/``answers`` are constructed to deterministically hit a target
Score_Band through the real ``grade_quiz`` → ``band`` path, so the band passed
into ``decide_next`` is exactly the one under test.

Three cases:

(a) **Foundational DESCEND → REINFORCE coercion (Req 5.4).** On a DESCEND band,
    when ``decide_next`` proposes a ``next_node`` already in Path, equal to the
    current node, or no valid prerequisite at all (``action`` REINFORCE /
    ``next_node`` null), ``decide`` cannot descend further: it coerces the
    action to REINFORCE, keeps the learner on the current node, and marks that
    foundational node as the Floor_Node.

(b) **Band-inconsistent action override (Req 5.10).** When the band computed in
    code is REINFORCE or CLIMB but ``decide_next`` returns a different
    ``action``, ``decide`` overrides the resolved action to match the passed
    band rather than trusting the LLM.

(c) **`decide_next` failure after 2 retries (Req 5.11).** When ``decide_next``
    raises on every attempt, ``decide`` retries the initial attempt plus
    ``DECIDE_NEXT_MAX_RETRIES`` times, then raises ``DecideError`` and leaves the
    caller's Path unchanged.

Validates: Requirements 5.4, 5.10, 5.11
"""
from unittest.mock import patch

import pytest

from app.services import game


# ---------------------------------------------------------------------------
# Quiz builders — every MCQ has correct_index 0, so an answer of 0 is correct
# and 1 is incorrect. This lets each test pin score_pct = correct/total and
# therefore the exact Score_Band that decide() computes and passes in.
# ---------------------------------------------------------------------------

def _question(i: int) -> dict:
    """A minimal valid MCQ (correct option is index 0) carrying a concept_tag."""
    return {
        "question": f"Question {i}?",
        "options": [f"Right {i}", f"Wrong {i}", f"Other {i}"],
        "correct_index": 0,
        "explanation": f"Option 0 is correct for {i}.",
        "concept_tag": f"concept-{i}",
    }


def _quiz_for_band(target: str) -> tuple[list[dict], list[int]]:
    """Build (questions, answers) whose graded score lands in ``target`` band.

    - DESCEND   (< 0.40):   3 questions, 0 correct  → 0.0
    - REINFORCE (0.40-0.70): 2 questions, 1 correct  → 0.5
    - CLIMB     (> 0.70):   4 questions, 4 correct  → 1.0
    """
    if target == "DESCEND":
        questions = [_question(0), _question(1), _question(2)]
        answers = [1, 1, 1]  # all wrong → 0.0
    elif target == "REINFORCE":
        questions = [_question(0), _question(1)]
        answers = [0, 1]  # 1/2 → 0.5
    elif target == "CLIMB":
        questions = [_question(0), _question(1), _question(2), _question(3)]
        answers = [0, 0, 0, 0]  # 4/4 → 1.0
    else:  # pragma: no cover - guard against typos in tests
        raise AssertionError(f"unknown target band {target!r}")
    return questions, answers


def _raw_decision(action: str, next_node, *, reached_goal: bool = False) -> dict:
    """A raw ``decide_next`` response dict in the documented shape."""
    return {
        "action": action,
        "next_node": next_node,
        "reached_goal": reached_goal,
        "gap": "the targeted concept",
        "diagnosis": "You are close; here is the next step.",
    }


# ---------------------------------------------------------------------------
# Sanity: confirm the quiz builders actually produce the intended bands so the
# coercion/override assertions below are exercising the right code paths.
# ---------------------------------------------------------------------------

class TestQuizBuildersHitTargetBands:
    @pytest.mark.parametrize("target", ["DESCEND", "REINFORCE", "CLIMB"])
    def test_builder_lands_in_target_band(self, target):
        questions, answers = _quiz_for_band(target)
        result = game.grade_quiz(questions, answers)
        assert game.band(result.score_pct) == target


# ---------------------------------------------------------------------------
# (a) Foundational DESCEND → REINFORCE coercion (Req 5.4).
# ---------------------------------------------------------------------------

class TestFoundationalDescendCoercedToReinforce:
    def test_next_node_in_path_coerces_reinforce_and_marks_floor(self):
        # DESCEND band, but the proposed prerequisite is already in Path: no
        # valid band-consistent candidate remains, so coerce REINFORCE and mark
        # the current node as the Floor_Node (Req 5.4, 6.3, 6.5).
        questions, answers = _quiz_for_band("DESCEND")
        req = game.DecideRequest(
            goal="backtracking",
            current_node="recursion",
            path=["backtracking", "recursion", "function calls"],
            questions=questions,
            answers=answers,
            floor_node=None,
            descent_depth=1,
        )
        # decide_next proposes a node already visited.
        with patch.object(
            game, "decide_next", return_value=_raw_decision("DESCEND", "function calls")
        ):
            resp = game.decide(req)

        assert resp.band == "DESCEND"
        assert resp.action == "REINFORCE"
        assert resp.next_node == "recursion"
        assert resp.reached_goal is False
        # The foundational node becomes the floor.
        assert resp.floor_node == "recursion"

    def test_next_node_equal_to_current_coerces_reinforce(self):
        # DESCEND band, but decide_next returns next_node == current_node — the
        # prompt's signal that the current node is already foundational. Coerce
        # REINFORCE on the current node and mark it the floor (Req 5.4).
        questions, answers = _quiz_for_band("DESCEND")
        req = game.DecideRequest(
            goal="backtracking",
            current_node="variables",
            path=["backtracking", "variables"],
            questions=questions,
            answers=answers,
            floor_node=None,
            descent_depth=2,
        )
        with patch.object(
            game, "decide_next", return_value=_raw_decision("DESCEND", "variables")
        ):
            resp = game.decide(req)

        assert resp.band == "DESCEND"
        assert resp.action == "REINFORCE"
        assert resp.next_node == "variables"
        assert resp.floor_node == "variables"

    def test_no_valid_prerequisite_coerces_reinforce(self):
        # DESCEND band, but decide_next signals foundational explicitly (action
        # REINFORCE, next_node null): there is no more fundamental prerequisite,
        # so reinforce in place rather than descend further (Req 5.4).
        questions, answers = _quiz_for_band("DESCEND")
        req = game.DecideRequest(
            goal="the French Revolution",
            current_node="what a monarchy is",
            path=["the French Revolution", "what a monarchy is"],
            questions=questions,
            answers=answers,
            floor_node=None,
            descent_depth=3,
        )
        with patch.object(
            game, "decide_next", return_value=_raw_decision("REINFORCE", None)
        ):
            resp = game.decide(req)

        assert resp.band == "DESCEND"
        assert resp.action == "REINFORCE"
        assert resp.next_node == "what a monarchy is"
        assert resp.floor_node == "what a monarchy is"

    def test_existing_floor_is_not_moved_on_coercion(self):
        # When a floor was already established, coercion does not move it
        # (the floor is marked exactly once, Req 6.2).
        questions, answers = _quiz_for_band("DESCEND")
        req = game.DecideRequest(
            goal="backtracking",
            current_node="recursion",
            path=["backtracking", "recursion"],
            questions=questions,
            answers=answers,
            floor_node="already-the-floor",
            descent_depth=1,
        )
        with patch.object(
            game, "decide_next", return_value=_raw_decision("REINFORCE", None)
        ):
            resp = game.decide(req)

        assert resp.action == "REINFORCE"
        assert resp.floor_node == "already-the-floor"


# ---------------------------------------------------------------------------
# (b) Band-inconsistent action override (Req 5.10).
# ---------------------------------------------------------------------------

class TestBandInconsistentActionOverride:
    def test_reinforce_band_overrides_climb_action(self):
        # Band computed as REINFORCE, but decide_next returns a CLIMB action
        # with a forward node. decide overrides the action to match the band:
        # the learner stays on the current node (Req 5.10, 7.1).
        questions, answers = _quiz_for_band("REINFORCE")
        req = game.DecideRequest(
            goal="backtracking",
            current_node="recursion",
            path=["backtracking", "recursion"],
            questions=questions,
            answers=answers,
        )
        with patch.object(
            game, "decide_next", return_value=_raw_decision("CLIMB", "backtracking")
        ):
            resp = game.decide(req)

        assert resp.band == "REINFORCE"
        assert resp.action == "REINFORCE"
        assert resp.next_node == "recursion"
        assert resp.reached_goal is False

    def test_reinforce_band_overrides_descend_action(self):
        # The override holds for the other inconsistent direction too: a DESCEND
        # action under a REINFORCE band is overridden to REINFORCE (Req 5.10).
        questions, answers = _quiz_for_band("REINFORCE")
        req = game.DecideRequest(
            goal="backtracking",
            current_node="recursion",
            path=["backtracking", "recursion"],
            questions=questions,
            answers=answers,
        )
        with patch.object(
            game, "decide_next", return_value=_raw_decision("DESCEND", "function calls")
        ):
            resp = game.decide(req)

        assert resp.band == "REINFORCE"
        assert resp.action == "REINFORCE"
        assert resp.next_node == "recursion"

    def test_climb_band_overrides_descend_action(self):
        # Band computed as CLIMB, but decide_next returns a DESCEND action. The
        # band wins: with a valid forward node not in Path, decide resolves a
        # CLIMB advancing exactly one step (Req 5.10, 8.1).
        questions, answers = _quiz_for_band("CLIMB")
        req = game.DecideRequest(
            goal="backtracking",
            current_node="recursion",
            path=["backtracking", "recursion"],
            questions=questions,
            answers=answers,
        )
        with patch.object(
            game, "decide_next", return_value=_raw_decision("DESCEND", "permutations")
        ):
            resp = game.decide(req)

        assert resp.band == "CLIMB"
        assert resp.action == "CLIMB"
        assert resp.next_node == "permutations"
        assert resp.reached_goal is False


# ---------------------------------------------------------------------------
# (c) decide_next failure after 2 retries (Req 5.11).
# ---------------------------------------------------------------------------

class TestDecideNextFailureLeavesPathUnchanged:
    def test_raises_decide_error_after_retries(self):
        # decide_next raises on every attempt → DecideError after the initial
        # attempt plus DECIDE_NEXT_MAX_RETRIES retries (Req 5.11).
        questions, answers = _quiz_for_band("DESCEND")
        req = game.DecideRequest(
            goal="backtracking",
            current_node="recursion",
            path=["backtracking", "recursion"],
            questions=questions,
            answers=answers,
        )
        with patch.object(
            game, "decide_next", side_effect=RuntimeError("LLM timeout")
        ) as stub:
            with pytest.raises(game.DecideError):
                game.decide(req)

        # Initial attempt + DECIDE_NEXT_MAX_RETRIES retries were made.
        assert stub.call_count == 1 + game.DECIDE_NEXT_MAX_RETRIES

    def test_path_is_left_unchanged_on_failure(self):
        # The caller's Path must be untouched when decide_next fails, so the
        # learner can retry without a corrupted session (Req 5.11).
        questions, answers = _quiz_for_band("DESCEND")
        original_path = ["backtracking", "recursion"]
        req = game.DecideRequest(
            goal="backtracking",
            current_node="recursion",
            path=original_path,
            questions=questions,
            answers=answers,
        )
        with patch.object(game, "decide_next", side_effect=RuntimeError("boom")):
            with pytest.raises(game.DecideError):
                game.decide(req)

        # Same contents and same object identity: nothing was appended/mutated.
        assert req.path == ["backtracking", "recursion"]
        assert original_path == ["backtracking", "recursion"]

    def test_non_object_response_is_treated_as_failure(self):
        # A non-dict response from decide_next is a failed attempt; after the
        # retries are exhausted it surfaces as DecideError (Req 5.11).
        questions, answers = _quiz_for_band("DESCEND")
        req = game.DecideRequest(
            goal="backtracking",
            current_node="recursion",
            path=["backtracking", "recursion"],
            questions=questions,
            answers=answers,
        )
        with patch.object(
            game, "decide_next", return_value="not a dict"
        ) as stub:
            with pytest.raises(game.DecideError):
                game.decide(req)

        assert stub.call_count == 1 + game.DECIDE_NEXT_MAX_RETRIES
