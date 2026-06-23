"""Property-based test for marking the Floor_Node once on the first climb.

# Feature: adaptive-learning-game, Property 5: The floor is marked once on the first climb out of a descent

For any sequence of bands containing at least one ``DESCEND`` followed by a
``CLIMB``, the ``Floor_Node`` is set to the node that was climbed out of and is
set exactly once for the session (subsequent climbs do not move the floor).

The backend is stateless, so a session is simulated by threading ``floor_node``
and ``descent_depth`` between successive :func:`game.decide` calls. The band of
each step is driven deterministically through the constructed quiz answers
(all-wrong -> ``DESCEND``, half -> ``REINFORCE``, all-correct -> ``CLIMB``), and
the raw, untrusted ``decide_next`` LLM function is stubbed via
``unittest.mock.patch`` so each step proposes a controlled, never-repeating
``next_node`` consistent with the code-computed band.

Validates: Requirements 6.2
Properties: 5
"""
from unittest.mock import patch

from hypothesis import given, settings, strategies as st

from app.services import game
from app.services.game import DecideRequest, decide

GOAL = "goal-concept"
# The session always starts on a node that is NOT the goal, so a CLIMB never
# resolves to reached_goal (which would short-circuit before floor marking) —
# it is always a genuine "climb out of a descent" step (Req 6.2).
START_NODE = "start-node"


def _quiz_for_band(target: str) -> tuple[list[dict], list[int]]:
    """Build a 2-question quiz whose grade lands in the target band's range.

    ``grade_quiz`` -> ``band`` maps the score fraction deterministically:
    all-wrong (0.0) -> DESCEND, one-of-two (0.5) -> REINFORCE, all-correct
    (1.0) -> CLIMB. The questions carry distinct Concept_Tags and a fixed
    ``correct_index`` of 0.
    """
    questions = [
        {"concept_tag": "tag-0", "correct_index": 0, "options": ["a", "b"]},
        {"concept_tag": "tag-1", "correct_index": 0, "options": ["a", "b"]},
    ]
    if target == "DESCEND":
        answers = [1, 1]  # both wrong -> 0.0
    elif target == "CLIMB":
        answers = [0, 0]  # both correct -> 1.0
    else:  # REINFORCE
        answers = [0, 1]  # one correct -> 0.5
    return questions, answers


def _make_decide_next_stub():
    """A stubbed ``decide_next`` that proposes a fresh node per band.

    Each DESCEND/CLIMB step returns a uniquely named ``next_node`` that has never
    appeared in the path, so the ``decide`` orchestrator treats it as a genuine
    descent/climb rather than coercing REINFORCE. REINFORCE keeps the learner on
    the current node. The action always matches the code-computed band passed in.
    """
    counter = {"n": 0}

    def stub(goal, current_node, path, score_pct, band_value, missed, correct):
        if band_value == "REINFORCE":
            return {
                "action": "REINFORCE",
                "next_node": current_node,
                "reached_goal": False,
                "gap": "gap",
                "diagnosis": "diagnosis",
            }
        counter["n"] += 1
        prefix = "prereq" if band_value == "DESCEND" else "advance"
        return {
            "action": band_value,
            "next_node": f"{prefix}-{counter['n']}",
            "reached_goal": False,
            "gap": "gap",
            "diagnosis": "diagnosis",
        }

    return stub


def _run_session(bands: list[str]) -> list[dict]:
    """Drive ``decide`` through a band sequence, threading session state.

    Returns one record per step capturing the band realized, the current node
    and floor/depth going IN, and the floor going OUT — enough to assert the
    floor is marked once, to the climbed node, on the first climb out of a
    descent.
    """
    stub = _make_decide_next_stub()
    current_node = START_NODE
    path = [current_node]
    floor_node: str | None = None
    descent_depth = 0
    steps: list[dict] = []

    with patch.object(game, "decide_next", stub):
        for target_band in bands:
            questions, answers = _quiz_for_band(target_band)
            req = DecideRequest(
                goal=GOAL,
                current_node=current_node,
                path=list(path),
                questions=questions,
                answers=answers,
                floor_node=floor_node,
                descent_depth=descent_depth,
            )
            resp = decide(req)

            steps.append(
                {
                    "band": resp.band,
                    "in_current": current_node,
                    "in_floor": floor_node,
                    "in_depth": descent_depth,
                    "out_floor": resp.floor_node,
                    "next_node": resp.next_node,
                }
            )

            # Thread state to the next step, mirroring the stateless client loop.
            floor_node = resp.floor_node
            descent_depth = resp.descent_depth
            if resp.next_node is not None and (
                game._norm_node(resp.next_node) != game._norm_node(current_node)
            ):
                current_node = resp.next_node
                path.append(current_node)

    return steps


@st.composite
def _bands_descend_then_climb(draw):
    """A band sequence with >=1 DESCEND strictly before the first CLIMB.

    A prefix of 1-4 DESCENDs guarantees the descent depth is positive by the
    first CLIMB; a random tail (kept small so the total descent count stays well
    under MAX_DESCENT_DEPTH) exercises floor-stability across later steps.
    """
    n_descend = draw(st.integers(min_value=1, max_value=4))
    tail = draw(
        st.lists(
            st.sampled_from(["DESCEND", "REINFORCE", "CLIMB"]),
            min_size=0,
            max_size=5,
        )
    )
    return ["DESCEND"] * n_descend + ["CLIMB"] + tail


class TestFloorMarkedOnceOnFirstClimb:
    @settings(max_examples=100)
    @given(bands=_bands_descend_then_climb())
    def test_floor_marked_once_to_climbed_node(self, bands):
        steps = _run_session(bands)

        # Locate the first CLIMB taken out of a descent (descent_depth > 0).
        first_climb_idx = next(
            i
            for i, s in enumerate(steps)
            if s["band"] == "CLIMB" and s["in_depth"] > 0
        )
        first_climb = steps[first_climb_idx]

        # Before that climb, no floor has been established: every genuine
        # descent leaves the floor untouched (Req 6.2).
        assert first_climb["in_floor"] is None
        for s in steps[:first_climb_idx]:
            assert s["out_floor"] is None

        # The floor is set to the node climbed OUT of — the current node at the
        # moment of the first climb (Req 6.2).
        floor = first_climb["out_floor"]
        assert floor == first_climb["in_current"]
        assert floor is not None

        # Set exactly once: no later step moves the floor off that node, no
        # matter how many further climbs/descents/reinforces occur.
        for s in steps[first_climb_idx:]:
            assert s["out_floor"] == floor

    def test_single_descend_then_climb_marks_that_node(self):
        # Minimal explicit case: descend once to a prerequisite, then climb out
        # of it. The floor is the prerequisite the learner first passed.
        steps = _run_session(["DESCEND", "CLIMB"])

        descend_step, climb_step = steps
        assert descend_step["band"] == "DESCEND"
        # No floor after the genuine descent.
        assert descend_step["out_floor"] is None
        # The climb marks the floor to the node it climbed out of.
        assert climb_step["band"] == "CLIMB"
        assert climb_step["in_depth"] > 0
        assert climb_step["out_floor"] == climb_step["in_current"]

    def test_floor_not_moved_by_a_second_climb(self):
        # A later climb must not relocate the floor (set exactly once, Req 6.2).
        steps = _run_session(["DESCEND", "CLIMB", "CLIMB"])

        floors = [s["out_floor"] for s in steps]
        assert floors[0] is None
        assert floors[1] is not None
        # The second climb leaves the floor exactly where the first put it.
        assert floors[2] == floors[1]
