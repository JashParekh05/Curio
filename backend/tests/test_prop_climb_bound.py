"""Property-based test for the climb bound in ``decide``.

# Feature: adaptive-learning-game, Property 4: Climb is bounded by the goal

For any CLIMB decision, the service never emits a ``next_node`` past the
Goal_Node, and ``reached_goal`` is true exactly when the current node is the
Goal_Node.

This exercises the real ``decide`` orchestration with a CLIMB band forced in
code (the quiz is graded to ``score_pct > 0.70``) and the raw ``decide_next``
LLM function stubbed via ``unittest.mock.patch`` to return CLIMB decisions with
a proposed ``next_node``. We assert the in-code guarantees the prompt cannot be
trusted to keep:

- When ``current_node == goal``: ``reached_goal`` is true and ``next_node`` is
  None — the climb stops at the goal and never advances past it (Req 5.6, 8.2).
- When ``current_node != goal``: ``reached_goal`` is false and the climb
  advances by at most one node — either the proposed one-step ``next_node``
  (Req 5.5, 8.1, 8.3) or, when the proposal is degenerate (repeats the path,
  equals the current node, or is missing), a coercion to REINFORCE on the
  current node (Req 5.9).

The stubbed ``reached_goal`` from the LLM is randomized to confirm the code
derives ``reached_goal`` from ``current_node == goal`` rather than trusting the
model.

Validates: Requirements 5.5, 5.6
"""
import math
from unittest.mock import patch

from hypothesis import assume, given, settings, strategies as st

from app.services import game
from app.services.game import DecideRequest


# A pool of clearly distinct node names so path membership and node identity are
# unambiguous under the service's normalized (trimmed, case-insensitive) compare.
_POOL = [f"concept-{c}" for c in "abcdefgh"]


@st.composite
def _climbing_answers(draw):
    """A non-empty quiz plus answers that grade to ``score_pct > 0.70`` (CLIMB).

    Generates ``n`` tagged MCQs (2-4 options each) and answers the first
    ``correct`` of them right and the rest wrong, where ``correct / n`` is
    strictly greater than 0.70 so :func:`game.band` yields ``CLIMB``. ``n`` is
    capped at 10 so the 4-decimal-rounded score stays comfortably above the
    0.70 threshold for every drawn shape.
    """
    n = draw(st.integers(min_value=1, max_value=10))
    correct = draw(st.integers(min_value=math.floor(0.70 * n) + 1, max_value=n))
    questions: list[dict] = []
    answers: list[int] = []
    for i in range(n):
        num_options = draw(st.integers(min_value=2, max_value=4))
        correct_index = draw(st.integers(min_value=0, max_value=num_options - 1))
        questions.append(
            {
                "concept_tag": f"concept-{i}",
                "correct_index": correct_index,
                "options": [f"opt{j}" for j in range(num_options)],
            }
        )
        if i < correct:
            answers.append(correct_index)  # answered correctly
        else:
            answers.append((correct_index + 1) % num_options)  # answered wrong
    return questions, answers


def _climb_stub(next_node, reached_goal):
    """A raw ``decide_next`` decision dict on the CLIMB band."""
    return {
        "action": "CLIMB",
        "next_node": next_node,
        "reached_goal": reached_goal,
        "gap": "the next concept toward the goal",
        "diagnosis": "You have this down; you are moving up one step.",
    }


@st.composite
def _at_goal_scenario(draw):
    """Current node IS the goal; the path and the LLM's proposal are arbitrary."""
    goal = draw(st.sampled_from(_POOL))
    path = draw(st.lists(st.sampled_from(_POOL), unique=True, max_size=6))
    llm_next = draw(st.one_of(st.none(), st.sampled_from(_POOL)))
    llm_reached = draw(st.booleans())
    descent_depth = draw(st.integers(min_value=0, max_value=12))
    return goal, path, llm_next, llm_reached, descent_depth


@st.composite
def _valid_climb_scenario(draw):
    """Current node differs from the goal and the LLM proposes a genuine one-step
    climb: a node not equal to the current node and not already in the path."""
    names = draw(st.lists(st.sampled_from(_POOL), unique=True, min_size=3, max_size=8))
    goal, current, proposed = names[0], names[1], names[2]
    rest = names[3:]
    # Path drawn from nodes that are neither the current node nor the proposed
    # next node (it may include the goal). This keeps the proposal a valid,
    # non-repeating one-step climb.
    path = draw(st.lists(st.sampled_from(rest + [goal]), unique=True))
    llm_reached = draw(st.booleans())
    descent_depth = draw(st.integers(min_value=0, max_value=12))
    return goal, current, proposed, path, llm_reached, descent_depth


@st.composite
def _degenerate_climb_scenario(draw):
    """Current node differs from the goal but the LLM's CLIMB proposal is
    degenerate — None, equal to the current node, or already in the path — so
    the service has no valid one-step candidate and coerces REINFORCE."""
    names = draw(st.lists(st.sampled_from(_POOL), unique=True, min_size=3, max_size=8))
    goal, current, other = names[0], names[1], names[2]
    rest = names[3:]
    base_path = draw(st.lists(st.sampled_from(rest + [goal]), unique=True))
    kind = draw(st.sampled_from(["none", "equals_current", "in_path"]))
    if kind == "none":
        proposed, path = None, base_path
    elif kind == "equals_current":
        proposed, path = current, base_path
    else:  # in_path: ensure 'other' is in the path and propose it
        path = base_path + [other]
        proposed = other
    llm_reached = draw(st.booleans())
    descent_depth = draw(st.integers(min_value=0, max_value=12))
    return goal, current, proposed, path, llm_reached, descent_depth


class TestClimbIsBoundedByTheGoal:
    @settings(max_examples=100)
    @given(quiz=_climbing_answers(), scenario=_at_goal_scenario())
    def test_reached_goal_true_exactly_at_goal(self, quiz, scenario):
        # When the current node IS the goal, the climb stops: reached_goal is
        # true and next_node is None, regardless of what the LLM proposed.
        questions, answers = quiz
        goal, path, llm_next, llm_reached, descent_depth = scenario
        assume(game.band(round(sum(1 for q, a in zip(questions, answers)
                                   if a == q["correct_index"]) / len(questions), 4)) == "CLIMB")

        with patch.object(game, "decide_next", return_value=_climb_stub(llm_next, llm_reached)):
            resp = game.decide(
                DecideRequest(
                    goal=goal,
                    current_node=goal,  # current node IS the goal
                    path=path,
                    questions=questions,
                    answers=answers,
                    descent_depth=descent_depth,
                )
            )

        assert resp.band == "CLIMB"
        assert resp.action == "CLIMB"
        assert resp.reached_goal is True
        # Never advances past the goal — the climb terminates with no next node.
        assert resp.next_node is None

    @settings(max_examples=100)
    @given(quiz=_climbing_answers(), scenario=_valid_climb_scenario())
    def test_valid_climb_advances_exactly_one_node_below_goal(self, quiz, scenario):
        # Below the goal with a genuine one-step proposal: advance exactly one
        # node toward the goal and reached_goal is false.
        questions, answers = quiz
        goal, current, proposed, path, llm_reached, descent_depth = scenario
        assume(game.band(round(sum(1 for q, a in zip(questions, answers)
                                   if a == q["correct_index"]) / len(questions), 4)) == "CLIMB")

        with patch.object(game, "decide_next", return_value=_climb_stub(proposed, llm_reached)):
            resp = game.decide(
                DecideRequest(
                    goal=goal,
                    current_node=current,
                    path=path,
                    questions=questions,
                    answers=answers,
                    descent_depth=descent_depth,
                )
            )

        assert resp.band == "CLIMB"
        # current_node != goal, so reached_goal is false regardless of the LLM.
        assert resp.reached_goal is False
        # The climb advances by exactly one node: the proposed next node.
        assert resp.action == "CLIMB"
        assert resp.next_node == proposed

    @settings(max_examples=100)
    @given(quiz=_climbing_answers(), scenario=_degenerate_climb_scenario())
    def test_degenerate_climb_below_goal_coerces_reinforce(self, quiz, scenario):
        # Below the goal with a degenerate proposal (repeats the path, equals the
        # current node, or missing): advance at most one node — here zero, by
        # coercing REINFORCE on the current node — and reached_goal is false.
        questions, answers = quiz
        goal, current, proposed, path, llm_reached, descent_depth = scenario
        assume(game.band(round(sum(1 for q, a in zip(questions, answers)
                                   if a == q["correct_index"]) / len(questions), 4)) == "CLIMB")

        with patch.object(game, "decide_next", return_value=_climb_stub(proposed, llm_reached)):
            resp = game.decide(
                DecideRequest(
                    goal=goal,
                    current_node=current,
                    path=path,
                    questions=questions,
                    answers=answers,
                    descent_depth=descent_depth,
                )
            )

        assert resp.band == "CLIMB"
        assert resp.reached_goal is False
        # No valid one-step candidate, so the learner stays on the current node.
        assert resp.action == "REINFORCE"
        assert resp.next_node == current
