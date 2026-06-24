"""Property-based + example tests for ``game.candidate_paths`` (branching hook).

# Feature: pixel-quest-adventure, branching candidate enumeration

``candidate_paths`` is the additive Phase 2 hook backing ``GET /api/game/paths``.
It reuses the existing CLIMB ``decide_next`` logic to surface a small set of
alternative on-goal next Stages the Play_Surface can offer as forks (Req 14.1).
It does NOT change ``decide``/``deliver_node``; when it produces fewer than two
(or none) the client falls back to the single engine-chosen node (Req 14.3).

These tests stub the raw ``decide_next`` LLM call via ``unittest.mock.patch``
(the same pattern the existing ``decide`` tests use — no real call is made) and
pin the guarantees the helper enforces in code:

- Candidates are **valid** (normalized, non-empty) and never the current node.
- Candidates are **never in the provided ``path``** and never repeat each other
  (Req 14.3).
- Candidates are **never past the goal**: collection stops once the goal is
  proposed, so the goal — when offered — is always the last candidate (Req 14.3).
- The list is **capped at ``BRANCH_CANDIDATE_COUNT``** (and at the configured
  ``max_candidates``).
- When the learner is already at the goal there are no candidates (Req 14.3).
- A ``decide_next`` failure is best-effort: collection stops and whatever valid
  candidates were gathered so far are returned (Req 14.3).

Validates: Requirements 14.1, 14.2, 14.3
"""
from unittest.mock import patch

from hypothesis import given, settings, strategies as st

from app.services import game


# A pool of clearly distinct node names so path membership and node identity are
# unambiguous under the service's normalized (trimmed, case-insensitive) compare.
_POOL = [f"concept-{c}" for c in "abcdefghij"]


def _fresh_node_stub(supply: list[str], current: str):
    """A ``decide_next`` side effect that proposes genuine one-step climbs.

    Mirrors the real LLM contract the helper relies on: each call returns a node
    from ``supply`` that is not already in the working ``path`` argument and is
    not the current node. When nothing fresh remains it returns a null proposal,
    which the helper treats as "no further candidate". The working path passed by
    ``candidate_paths`` grows with each accepted candidate, so this naturally
    yields a distinct node per call.
    """

    def _side_effect(goal, current_node, path, score_pct, band_value,
                     missed, correct):
        for node in supply:
            if game._norm_node(node) == game._norm_node(current_node):
                continue
            if game._in_path(node, path):
                continue
            return {
                "action": "CLIMB",
                "next_node": node,
                "reached_goal": game._norm_node(node) == game._norm_node(goal),
                "gap": "the next concept toward the goal",
                "diagnosis": "Advancing one step.",
            }
        return {
            "action": "CLIMB",
            "next_node": None,
            "reached_goal": False,
            "gap": "",
            "diagnosis": "",
        }

    return _side_effect


@st.composite
def _branch_scenario(draw):
    """A goal/current/path plus a fresh-node supply for the stubbed LLM.

    ``current`` differs from ``goal``; ``path`` is a unique subset of the pool
    that may include the goal and/or the current node; ``supply`` is the set of
    pool nodes the stubbed ``decide_next`` may propose, optionally including the
    goal so the on-goal stop is exercised.
    """
    names = draw(st.lists(st.sampled_from(_POOL), unique=True,
                          min_size=3, max_size=len(_POOL)))
    goal, current = names[0], names[1]
    rest = names[2:]
    path = draw(st.lists(st.sampled_from(rest + [goal]), unique=True))
    # The supply is the fresh nodes (not in path, not current); optionally allow
    # the goal to be proposed so the goal-stop branch is hit.
    include_goal = draw(st.booleans())
    supply = [
        n for n in rest
        if not game._in_path(n, path)
        and game._norm_node(n) != game._norm_node(current)
    ]
    if include_goal and not game._in_path(goal, path):
        supply = [goal] + supply
    max_candidates = draw(st.integers(min_value=0, max_value=5))
    return goal, current, path, supply, max_candidates


class TestCandidatePathsInvariants:
    @settings(max_examples=150)
    @given(scenario=_branch_scenario())
    def test_candidates_are_valid_nonrepeating_and_on_goal(self, scenario):
        goal, current, path, supply, max_candidates = scenario

        with patch.object(
            game, "decide_next", side_effect=_fresh_node_stub(supply, current)
        ):
            result = game.candidate_paths(
                goal=goal,
                current_node=current,
                path=path,
                max_candidates=max_candidates,
            )

        # Capped at the requested max (and never negative).
        assert len(result) <= max(0, max_candidates)

        # Every candidate is a valid, non-empty normalized node.
        for c in result:
            assert game._normalize_next_node(c) is not None

        # Never the current node.
        for c in result:
            assert game._norm_node(c) != game._norm_node(current)

        # Never in the provided path (Req 14.3).
        for c in result:
            assert not game._in_path(c, path)

        # No repeats among the candidates themselves (Req 14.3).
        normalized = [game._norm_node(c) for c in result]
        assert len(normalized) == len(set(normalized))

        # Every candidate came from what the engine proposed.
        supply_norm = {game._norm_node(n) for n in supply}
        for c in result:
            assert game._norm_node(c) in supply_norm

        # Never past the goal: if the goal is offered it is the final candidate
        # and nothing is collected after it (Req 14.3).
        goal_norm = game._norm_node(goal)
        if goal_norm in normalized:
            assert normalized[-1] == goal_norm
            assert normalized.count(goal_norm) == 1

    @settings(max_examples=100)
    @given(scenario=_branch_scenario())
    def test_default_cap_is_branch_candidate_count(self, scenario):
        # With the default max, the helper never returns more than the configured
        # BRANCH_CANDIDATE_COUNT, regardless of how many fresh nodes exist.
        goal, current, path, supply, _ = scenario

        with patch.object(
            game, "decide_next", side_effect=_fresh_node_stub(supply, current)
        ):
            result = game.candidate_paths(goal=goal, current_node=current, path=path)

        assert len(result) <= game.BRANCH_CANDIDATE_COUNT


class TestCandidatePathsExamples:
    def test_at_goal_returns_no_candidates(self):
        # Already at the goal — there is no on-goal node to advance to (Req 14.3).
        # decide_next must not even be consulted.
        with patch.object(game, "decide_next") as stub:
            result = game.candidate_paths(
                goal="backtracking",
                current_node="backtracking",
                path=["recursion", "backtracking"],
            )
        assert result == []
        stub.assert_not_called()

    def test_at_goal_case_insensitive(self):
        # The at-goal short-circuit uses the normalized compare.
        with patch.object(game, "decide_next") as stub:
            result = game.candidate_paths(
                goal="Backtracking",
                current_node="  backtracking ",
                path=[],
            )
        assert result == []
        stub.assert_not_called()

    def test_returns_distinct_fresh_candidates_up_to_cap(self):
        # Plenty of fresh nodes available → exactly BRANCH_CANDIDATE_COUNT
        # distinct candidates, none in the path.
        supply = ["loops", "functions", "arrays", "pointers", "graphs"]
        with patch.object(
            game, "decide_next", side_effect=_fresh_node_stub(supply, "recursion")
        ):
            result = game.candidate_paths(
                goal="backtracking",
                current_node="recursion",
                path=["recursion"],
            )
        assert len(result) == game.BRANCH_CANDIDATE_COUNT
        assert all(c not in {"recursion"} for c in result)
        assert len(result) == len({game._norm_node(c) for c in result})

    def test_proposal_in_path_is_never_offered(self):
        # If decide_next keeps proposing a node already visited, it is rejected
        # and nothing is collected (Req 14.3).
        raw = {
            "action": "CLIMB",
            "next_node": "loops",  # already in path
            "reached_goal": False,
            "gap": "x",
            "diagnosis": "y",
        }
        with patch.object(game, "decide_next", return_value=raw):
            result = game.candidate_paths(
                goal="backtracking",
                current_node="recursion",
                path=["recursion", "loops"],
            )
        assert result == []

    def test_proposal_equal_to_current_is_never_offered(self):
        # A proposal equal to the current node is not an advancement (Req 14.3).
        raw = {
            "action": "CLIMB",
            "next_node": "recursion",
            "reached_goal": False,
            "gap": "x",
            "diagnosis": "y",
        }
        with patch.object(game, "decide_next", return_value=raw):
            result = game.candidate_paths(
                goal="backtracking",
                current_node="recursion",
                path=["recursion"],
            )
        assert result == []

    def test_goal_proposal_is_final_and_stops_collection(self):
        # When the goal itself is proposed, it is a valid final candidate and
        # nothing past it is ever offered (Req 14.3).
        raw = {
            "action": "CLIMB",
            "next_node": "backtracking",  # the goal
            "reached_goal": True,
            "gap": "x",
            "diagnosis": "y",
        }
        with patch.object(game, "decide_next", return_value=raw) as stub:
            result = game.candidate_paths(
                goal="backtracking",
                current_node="recursion",
                path=["recursion"],
            )
        assert result == ["backtracking"]
        # Collection stopped at the goal rather than exhausting the budget.
        assert stub.call_count == 1

    def test_decide_next_failure_returns_gathered_so_far(self):
        # Best-effort: a decide_next failure stops collection and returns the
        # valid candidates gathered before the failure (Req 14.3).
        calls = {"n": 0}

        def _side_effect(goal, current_node, path, *rest):
            calls["n"] += 1
            if calls["n"] == 1:
                return {
                    "action": "CLIMB",
                    "next_node": "loops",
                    "reached_goal": False,
                    "gap": "x",
                    "diagnosis": "y",
                }
            raise RuntimeError("LLM timeout")

        with patch.object(game, "decide_next", side_effect=_side_effect):
            result = game.candidate_paths(
                goal="backtracking",
                current_node="recursion",
                path=["recursion"],
            )
        assert result == ["loops"]

    def test_immediate_failure_returns_empty(self):
        # A failure on the very first call yields an empty list, never an error.
        with patch.object(game, "decide_next", side_effect=RuntimeError("boom")):
            result = game.candidate_paths(
                goal="backtracking",
                current_node="recursion",
                path=["recursion"],
            )
        assert result == []

    def test_non_dict_response_returns_empty(self):
        # A non-dict response is treated as no candidate and stops collection.
        with patch.object(game, "decide_next", return_value="not a dict"):
            result = game.candidate_paths(
                goal="backtracking",
                current_node="recursion",
                path=["recursion"],
            )
        assert result == []

    def test_max_candidates_zero_returns_empty(self):
        # An explicit zero cap returns no candidates without consulting the LLM
        # for more than the budget allows.
        with patch.object(
            game, "decide_next",
            side_effect=_fresh_node_stub(["loops", "arrays"], "recursion"),
        ):
            result = game.candidate_paths(
                goal="backtracking",
                current_node="recursion",
                path=["recursion"],
                max_candidates=0,
            )
        assert result == []
