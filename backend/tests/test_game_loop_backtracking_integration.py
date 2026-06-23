"""End-to-end integration test for a CS topic ("backtracking") (Req 12.1, 12.2).

This drives a FULL descend → reinforce → climb loop end to end through the real
Game_Service orchestration — ``start_session`` → ``decide`` → ``deliver_node`` —
threading ``floor_node`` / ``descent_depth`` / ``path`` / ``current_node``
between ``decide`` calls exactly as the stateless Play_Surface client would. The
five LLM functions and the two youtube leaf functions are the only things
stubbed (via ``unittest.mock.patch``); everything that makes the loop *work* —
deterministic per-concept grading (``grade_quiz``), score banding (``band``),
descent no-repeat / foundational coercion, climb-bound / floor marking, clip
selection (``select_clip`` / ``to_embed_url``), and the transcript-grounded quiz
path — runs for real.

The scripted topic is the CS topic "backtracking". The loop exercises every
band:

1. **DESCEND** — the placement probe on the goal "backtracking" is failed
   (score 0.0), so the service descends to a more fundamental prerequisite
   ("recursion") and increments the descent depth.
2. **REINFORCE** — the learner is at the edge on "recursion" (score ~0.67), so
   the service keeps them on the same node and re-delivers it for a re-quiz.
3. **CLIMB (out of the descent)** — the learner masters "recursion" (score 1.0),
   so the service marks "recursion" as the Floor_Node (the first CLIMB out of a
   descent chain) and advances exactly one step back toward the goal.
4. **CLIMB → reached_goal** — the learner masters the goal node "backtracking"
   itself (score 1.0), so the service signals the session goal is reached.

The ``path`` threaded by the simulated client is the visited prerequisite /
climb chain below the goal; the no-repeat guard prevents re-descending into an
already-visited prerequisite while still allowing the climb back up to the goal,
where ``reached_goal`` fires once ``current_node == goal``.

Validates: Requirements 12.1, 12.2
"""
from dataclasses import dataclass, field
from unittest.mock import patch

from app.services import game


GOAL = "backtracking"
PREREQ = "recursion"


# ---------------------------------------------------------------------------
# Scripted LLM / youtube responses (stubbed boundaries)
# ---------------------------------------------------------------------------

def _probe_question(i: int, level: str) -> dict:
    """A valid placement-probe MCQ (correct option index 0) with tag + level."""
    return {
        "question": f"Which statement about concept {i} is correct?",
        "options": [f"Right answer {i}", f"Plausible miss {i}", f"Another miss {i}"],
        "correct_index": 0,
        "explanation": f"Option 0 is correct because of reason {i}.",
        "concept_tag": f"concept-{i}",
        "level": level,
    }


def _six_probe_questions() -> list[dict]:
    """A full 6-question probe with the ~2 prereq / 2 core / 2 stretch gradient."""
    return [
        _probe_question(0, "prerequisite"),
        _probe_question(1, "prerequisite"),
        _probe_question(2, "core"),
        _probe_question(3, "core"),
        _probe_question(4, "stretch"),
        _probe_question(5, "stretch"),
    ]


def _checkpoint_quiz(node: str) -> list[dict]:
    """A 3-question checkpoint quiz (correct option index 0) for a node."""
    return [
        {
            "question": f"What happens in scenario {i} for {node}?",
            "options": [f"Correct {i}", f"Wrong {i}", f"Other {i}"],
            "correct_index": 0,
            "explanation": f"Option 0 is right for {node} scenario {i}.",
            "concept_tag": f"{node}-concept-{i}",
        }
        for i in range(3)
    ]


def _clip() -> dict:
    """A raw youtube clip in the ideal 60-180s range so ``select_clip`` picks it."""
    return {
        "video_id": "vid12345678",
        "title": "Recursion in 3 Minutes",
        "channel_title": "Some Channel",
        "duration_seconds": 150,
        "has_caption": True,
        "view_count": 50_000,
        "thumbnail_url": "https://i.ytimg.com/vi/vid12345678/hq.jpg",
        "description": "A punchy explainer.",
    }


def _transcript_segments() -> list[dict]:
    return [
        {"start": 0.0, "duration": 2.0, "text": "Recursion is a function calling itself"},
        {"start": 2.0, "duration": 2.0, "text": "until it hits a base case"},
    ]


# The decisions the LLM returns at each of the four decide() turns, in order.
# decide() always calls decide_next exactly once on the success path, so one
# scripted dict is consumed per turn. The service computes the band in code from
# the score and overrides/enforces the structural guarantees regardless of these
# action values; they are written band-consistent for realism.
def _decide_script() -> list[dict]:
    return [
        # Turn 1: probe on the goal failed -> descend to a prerequisite.
        {
            "action": "DESCEND",
            "next_node": PREREQ,
            "reached_goal": False,
            "gap": "recursion",
            "diagnosis": "You stumble on recursion, which backtracking is built on.",
        },
        # Turn 2: at the edge on recursion -> reinforce in place.
        {
            "action": "REINFORCE",
            "next_node": PREREQ,
            "reached_goal": False,
            "gap": "base cases",
            "diagnosis": "You are close on recursion; let's shore up base cases.",
        },
        # Turn 3: mastered recursion -> climb one step back toward the goal.
        {
            "action": "CLIMB",
            "next_node": GOAL,
            "reached_goal": False,
            "gap": "applying recursion to backtracking",
            "diagnosis": "You've got recursion - now let's climb toward backtracking.",
        },
        # Turn 4: mastered the goal node itself -> reached the goal.
        {
            "action": "CLIMB",
            "next_node": None,
            "reached_goal": True,
            "gap": "",
            "diagnosis": "You've mastered backtracking. Goal reached!",
        },
    ]


# ---------------------------------------------------------------------------
# Simulated stateless client: threads floor_node / descent_depth / path /
# current_node between decide() calls exactly as the Play_Surface would.
# ---------------------------------------------------------------------------

@dataclass
class ClientState:
    goal: str
    current_node: str
    path: list[str] = field(default_factory=list)
    floor_node: str | None = None
    descent_depth: int = 0
    reached_goal: bool = False


def _grade_turn(state: ClientState, questions: list[dict], answers: list[int]) -> game.DecideResponse:
    """Run one decide() turn from the current client state, then thread the
    returned floor_node / descent_depth / path / current_node forward the way a
    stateless client would after rendering the outcome card."""
    req = game.DecideRequest(
        goal=state.goal,
        current_node=state.current_node,
        path=list(state.path),
        questions=questions,
        answers=answers,
        floor_node=state.floor_node,
        descent_depth=state.descent_depth,
    )
    resp = game.decide(req)

    # Persist the session fields the service threads back to the client.
    state.floor_node = resp.floor_node
    state.descent_depth = resp.descent_depth

    if resp.reached_goal:
        state.reached_goal = True
        return resp

    # On a genuine move (descend/climb to a new node), advance current_node and
    # append the visited node to the path. REINFORCE keeps the learner in place.
    if resp.action in ("DESCEND", "CLIMB") and resp.next_node and resp.next_node not in state.path:
        state.current_node = resp.next_node
        state.path.append(resp.next_node)
    else:  # REINFORCE (or a coerced stay) keeps the current node and path.
        state.current_node = resp.next_node or state.current_node

    return resp


# ---------------------------------------------------------------------------
# The integration test
# ---------------------------------------------------------------------------

class TestBacktrackingFullLoop:
    def test_descend_reinforce_climb_reaches_goal(self):
        with (
            patch.object(game, "generate_probe", return_value=_six_probe_questions()),
            patch.object(game, "decide_next", side_effect=_decide_script()),
            patch.object(game, "intuition", return_value={"hook": "Recursion is you, delegating to a smaller you."}),
            patch.object(game, "clip_query", return_value={"query": "recursion explained short"}),
            patch.object(game.youtube, "youtube_search", return_value=[_clip()]),
            patch.object(game.youtube, "_fetch_transcript", return_value=_transcript_segments()),
            patch.object(game, "generate_quiz", side_effect=lambda node, transcript: _checkpoint_quiz(node)),
            # Guard: on-the-fly node quizzes must never touch the DB orchestrator.
            patch.object(game.quiz, "generate_and_store_questions") as store_guard,
        ):
            # --- Start the session on the CS topic "backtracking" (Req 1, 2) ---
            session = game.start_session(GOAL)
            assert session.goal == GOAL
            assert session.current_node == GOAL  # placement starts AT the goal
            assert len(session.probe) == 6

            state = ClientState(goal=session.goal, current_node=session.current_node)
            actions: list[str] = []
            bands: list[str] = []

            # === Turn 1: fail the probe on the goal -> DESCEND to a prerequisite ===
            probe_answers = [1, 1, 1, 1, 1, 1]  # all wrong -> 0.0 -> DESCEND
            resp1 = _grade_turn(state, session.probe, probe_answers)
            actions.append(resp1.action)
            bands.append(resp1.band)

            assert resp1.band == "DESCEND"
            assert resp1.action == "DESCEND"
            assert resp1.next_node == PREREQ
            assert resp1.reached_goal is False
            # Descent recursion dropped to a more fundamental prerequisite and
            # deepened the descent (Req 6.1).
            assert state.current_node == PREREQ
            assert state.descent_depth == 1
            assert state.floor_node is None  # floor not set until the first climb out
            assert PREREQ in state.path

            # --- Deliver the prerequisite node (intuition + clip + quiz) ---
            node1 = game.deliver_node(state.current_node, state.goal)
            assert node1.node == PREREQ
            assert node1.hook  # Intuition_Card hook present (Req 9)
            assert node1.clip is not None
            # Clip exposed as a canonical embed URL (Req 10.3).
            assert node1.clip["video_url"] == game.to_embed_url("vid12345678")
            assert len(node1.quiz) == 3  # exactly 3 validated MCQs (Req 11.3)

            # === Turn 2: at the edge on recursion -> REINFORCE (stay) ===
            reinforce_answers = [0, 0, 1]  # 2/3 ~= 0.667 -> REINFORCE
            resp2 = _grade_turn(state, node1.quiz, reinforce_answers)
            actions.append(resp2.action)
            bands.append(resp2.band)

            assert resp2.band == "REINFORCE"
            assert resp2.action == "REINFORCE"
            # The learner is kept on the same node, not advanced or descended (Req 7.1).
            assert state.current_node == PREREQ
            assert resp2.next_node == PREREQ
            assert state.descent_depth == 1
            assert state.floor_node is None

            # --- Re-deliver the SAME node for the reinforce re-quiz (Req 7.2, 7.3) ---
            node2 = game.deliver_node(state.current_node, state.goal)
            assert node2.node == PREREQ
            assert len(node2.quiz) == 3  # a fresh checkpoint quiz on the same node

            # === Turn 3: master recursion -> CLIMB out of the descent, mark floor ===
            climb_answers = [0, 0, 0]  # 3/3 = 1.0 -> CLIMB
            resp3 = _grade_turn(state, node2.quiz, climb_answers)
            actions.append(resp3.action)
            bands.append(resp3.band)

            assert resp3.band == "CLIMB"
            assert resp3.action == "CLIMB"
            assert resp3.reached_goal is False
            # First CLIMB out of a descent chain marks the Floor_Node exactly once
            # at the node the learner just cleared (Req 6.2).
            assert resp3.floor_node == PREREQ
            assert state.floor_node == PREREQ
            # Climb advances exactly one step back toward the goal (Req 8.1, 8.3).
            assert resp3.next_node == GOAL
            assert state.current_node == GOAL

            # --- Deliver the goal node itself ---
            node3 = game.deliver_node(state.current_node, state.goal)
            assert node3.node == GOAL
            assert len(node3.quiz) == 3

            # === Turn 4: master the goal node -> reached_goal ===
            goal_answers = [0, 0, 0]  # 3/3 = 1.0 -> CLIMB, current == goal
            resp4 = _grade_turn(state, node3.quiz, goal_answers)
            actions.append(resp4.action)
            bands.append(resp4.band)

            assert resp4.band == "CLIMB"
            assert resp4.reached_goal is True  # current_node == goal (Req 5.6, 8.2)
            assert resp4.next_node is None
            assert state.reached_goal is True
            # The floor established during the descent is not moved by reaching
            # the goal (Req 6.2).
            assert state.floor_node == PREREQ

            # --- The loop exercised descend, reinforce, AND climb (Req 12.2) ---
            assert bands == ["DESCEND", "REINFORCE", "CLIMB", "CLIMB"]
            assert actions == ["DESCEND", "REINFORCE", "CLIMB", "CLIMB"]
            # The DB-coupled orchestrator was never called for node quizzes (Req 11.5).
            store_guard.assert_not_called()
