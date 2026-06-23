"""Property-based test for descent safety in the Game_Service.

# Feature: adaptive-learning-game, Property 3: Descent never repeats a node and bottoms out at REINFORCE

For any ``Path`` and any node the LLM proposes on a ``DESCEND`` band, the
resolved decision never selects a ``next_node`` already present in ``Path``; the
``Path`` it builds contains no duplicates; and when the current node is
foundational the action is coerced to ``REINFORCE`` rather than descending
further.

The band is driven to ``DESCEND`` by constructing a real failing quiz (a score
fraction strictly below 0.40 graded deterministically by ``grade_quiz``), and
``game.decide_next`` (the raw LLM call) is stubbed via ``unittest.mock.patch`` to
return a controlled ``{action, next_node, reached_goal, gap, diagnosis}`` so the
in-code descent guarantees in ``decide`` are exercised in isolation.

Validates: Requirements 5.3, 5.4, 6.3
"""
from unittest.mock import patch

from hypothesis import given, settings, strategies as st

from app.services import game

# A fixed pool of node names so paths, the current node, and the LLM's proposed
# node draw from the same space — making repeats and fresh prerequisites both
# reachable by the generator.
_NODE_POOL = [
    "recursion",
    "graphs",
    "trees",
    "arrays",
    "pointers",
    "induction",
    "sets",
    "logic",
    "loops",
    "functions",
    "backtracking",
    "dynamic-programming",
]

# The five ways the stubbed LLM can respond on a DESCEND band:
#   fresh             -> a genuine more-fundamental prerequisite not in Path
#   repeat            -> a node already in Path (must be rejected)
#   same_current      -> next_node == current_node (foundational signal)
#   null_next         -> next_node is null (foundational signal)
#   reinforce_action  -> action == REINFORCE (foundational signal, any node)
_PROPOSAL_KINDS = ["fresh", "repeat", "same_current", "null_next", "reinforce_action"]


@st.composite
def _descent_scenario(draw):
    """Generate a failing quiz (DESCEND band) plus a controlled LLM proposal.

    Returns the DecideRequest inputs together with the stubbed ``decide_next``
    payload so the test can both drive ``decide`` and re-derive the expected
    coercion.
    """
    # --- A quiz that grades to a DESCEND band (score_pct strictly < 0.40) -----
    total = draw(st.integers(min_value=3, max_value=8))
    # int(0.39 * total) keeps the correct fraction strictly below 0.40 for every
    # total in range (e.g. 1/3, 3/8 are both < 0.40).
    max_correct = int(0.39 * total)
    num_correct = draw(st.integers(min_value=0, max_value=max_correct))
    questions: list[dict] = []
    answers: list[int] = []
    for i in range(total):
        questions.append(
            {"concept_tag": f"c{i}", "correct_index": 0, "options": ["a", "b", "c"]}
        )
        # Answer option 0 (correct) for the first ``num_correct`` questions, a
        # wrong option otherwise — deterministic grading via quiz.grade.
        answers.append(0 if i < num_correct else 1)

    # --- Session context ------------------------------------------------------
    path = draw(st.lists(st.sampled_from(_NODE_POOL), unique=True, max_size=5))
    goal = draw(st.sampled_from(_NODE_POOL))
    current_node = draw(st.sampled_from(_NODE_POOL))
    descent_depth = draw(st.integers(min_value=0, max_value=12))

    # A node not in Path and distinct from the current node — a genuine deeper
    # prerequisite the descent is allowed to select.
    fresh_candidates = [
        n
        for n in _NODE_POOL
        if n not in path and game._norm_node(n) != game._norm_node(current_node)
    ]
    fresh = fresh_candidates[0] if fresh_candidates else "novel-prerequisite-xyz"

    # --- The controlled LLM proposal -----------------------------------------
    kind = draw(st.sampled_from(_PROPOSAL_KINDS))
    action = "DESCEND"
    next_node = fresh
    if kind == "repeat":
        next_node = draw(st.sampled_from(path)) if path else fresh
    elif kind == "same_current":
        next_node = current_node
    elif kind == "null_next":
        next_node = None
    elif kind == "reinforce_action":
        action = "REINFORCE"
        next_node = fresh

    return {
        "goal": goal,
        "current_node": current_node,
        "path": path,
        "questions": questions,
        "answers": answers,
        "descent_depth": descent_depth,
        "llm_action": action,
        "llm_next_node": next_node,
    }


class TestDescentSafety:
    @settings(max_examples=150)
    @given(scenario=_descent_scenario())
    def test_descent_never_repeats_and_bottoms_out_at_reinforce(self, scenario):
        raw_decision = {
            "action": scenario["llm_action"],
            "next_node": scenario["llm_next_node"],
            "reached_goal": False,
            "gap": "the missing prerequisite",
            "diagnosis": "You are missing a more fundamental idea.",
        }

        req = game.DecideRequest(
            goal=scenario["goal"],
            current_node=scenario["current_node"],
            path=scenario["path"],
            questions=scenario["questions"],
            answers=scenario["answers"],
            floor_node=None,
            descent_depth=scenario["descent_depth"],
        )

        with patch.object(game, "decide_next", return_value=raw_decision):
            result = game.decide(req)

        path = scenario["path"]
        current_node = scenario["current_node"]

        # The failing quiz always bands DESCEND, and a DESCEND scenario only ever
        # resolves to a genuine descent or a coerced REINFORCE.
        assert result.band == "DESCEND"
        assert result.action in ("DESCEND", "REINFORCE")

        if result.action == "DESCEND":
            # Core invariant: a genuine descent never selects a node already in
            # Path (Req 5.3, 6.3) and never sits on the current node.
            assert result.next_node is not None
            assert not game._in_path(result.next_node, path)
            assert game._norm_node(result.next_node) != game._norm_node(current_node)

            # The Path the descent builds (append the selected node) is
            # duplicate-free (Req 6.3).
            resulting_path = list(path) + [result.next_node]
            normalized = [game._norm_node(n) for n in resulting_path]
            assert len(normalized) == len(set(normalized))

            # A real descent only happens below the depth bound and advances it.
            assert scenario["descent_depth"] < game.MAX_DESCENT_DEPTH
            assert result.descent_depth == scenario["descent_depth"] + 1
        else:
            # Coerced REINFORCE: the learner stays on the current node and the
            # foundational floor is marked (Req 5.4, 6.5, 6.6).
            assert result.next_node == current_node
            assert result.floor_node is not None

        # Re-derive the expected coercion directly from the spec rules to pin
        # down each case exactly (foundational / repeat / depth-exhausted).
        foundational = (
            game._norm_node(scenario["llm_action"]).upper() == "REINFORCE"
            or scenario["llm_next_node"] is None
            or game._norm_node(scenario["llm_next_node"])
            == game._norm_node(current_node)
        )
        repeats_path = game._in_path(scenario["llm_next_node"], path)
        depth_exhausted = scenario["descent_depth"] >= game.MAX_DESCENT_DEPTH

        if foundational or repeats_path or depth_exhausted:
            # Any foundational signal, a path repeat, or an exhausted depth must
            # coerce to REINFORCE on the current node (never a repeated descent).
            assert result.action == "REINFORCE"
            assert result.next_node == current_node
        else:
            # A fresh, deeper prerequisite below the depth bound is a real
            # descent to exactly the proposed node.
            assert result.action == "DESCEND"
            assert result.next_node == scenario["llm_next_node"]

    def test_proposed_repeat_is_coerced_not_selected(self):
        # Explicit case: the LLM proposes a node already in Path on a DESCEND
        # band — decide must coerce REINFORCE, never echo the repeat (Req 5.3).
        questions = [
            {"concept_tag": f"c{i}", "correct_index": 0, "options": ["a", "b"]}
            for i in range(5)
        ]
        answers = [1, 1, 1, 1, 1]  # all wrong -> score 0.0 -> DESCEND
        req = game.DecideRequest(
            goal="backtracking",
            current_node="recursion",
            path=["recursion", "loops"],
            questions=questions,
            answers=answers,
        )
        raw = {
            "action": "DESCEND",
            "next_node": "loops",  # already in Path
            "reached_goal": False,
            "gap": "x",
            "diagnosis": "y",
        }
        with patch.object(game, "decide_next", return_value=raw):
            result = game.decide(req)

        assert result.band == "DESCEND"
        assert result.action == "REINFORCE"
        assert result.next_node == "recursion"
        assert not game._in_path(result.next_node, ["loops"])

    def test_foundational_node_coerces_reinforce_and_marks_floor(self):
        # The LLM signals a foundational node by echoing the current node — the
        # descent bottoms out at REINFORCE and the floor is marked (Req 5.4).
        questions = [
            {"concept_tag": f"c{i}", "correct_index": 0, "options": ["a", "b"]}
            for i in range(4)
        ]
        answers = [1, 1, 1, 1]  # all wrong -> DESCEND
        req = game.DecideRequest(
            goal="backtracking",
            current_node="logic",
            path=["backtracking", "recursion", "logic"],
            questions=questions,
            answers=answers,
            descent_depth=2,
        )
        raw = {
            "action": "REINFORCE",
            "next_node": "logic",
            "reached_goal": False,
            "gap": "x",
            "diagnosis": "y",
        }
        with patch.object(game, "decide_next", return_value=raw):
            result = game.decide(req)

        assert result.band == "DESCEND"
        assert result.action == "REINFORCE"
        assert result.next_node == "logic"
        assert result.floor_node == "logic"
