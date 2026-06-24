import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// Interaction tests for the Play_Surface Game_State_Machine (Task 8.5).
//
// These drive the PlayPage component through real DOM interactions and assert
// the two behaviors the task calls out:
//   (a) blank / whitespace-only topic submission is rejected and NO session is
//       started (Req 1.3); and
//   (b) entering a topic drives the full loop
//       probe → grading → decision → node-delivery → outcome → loop (Req 21.1).
//
// The network is never hit: `@/lib/api`'s three game functions
// (startGameSession / decideGame / deliverGameNode) and `@/lib/auth-context`'s
// useAuth are mocked. The real localStorage codec (lib/game-progress) runs
// against jsdom's storage; the active-session pointer is cleared between tests
// so a prior run never auto-restores into the next.

// --- mocks -----------------------------------------------------------------

vi.mock("@/lib/api", () => ({
  startGameSession: vi.fn(),
  decideGame: vi.fn(),
  deliverGameNode: vi.fn(),
  getGamePaths: vi.fn(),
}));

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ session: { access_token: "test-token" } }),
}));

import PlayPage from "./page";
import {
  startGameSession,
  decideGame,
  deliverGameNode,
  getGamePaths,
} from "@/lib/api";
import {
  clearActiveGameSessionId,
  type ProbeQuestion,
  type DecideResponse,
} from "@/lib/game-progress";

const mockStart = vi.mocked(startGameSession);
const mockDecide = vi.mocked(decideGame);
const mockDeliver = vi.mocked(deliverGameNode);
const mockGetPaths = vi.mocked(getGamePaths);

// --- test data builders ----------------------------------------------------

// Build `count` MCQs whose option texts are unique across the whole page so
// each option button can be targeted unambiguously by its accessible name.
function questions(prefix: string, count: number): ProbeQuestion[] {
  return Array.from({ length: count }, (_, i) => ({
    question: `${prefix} question ${i}?`,
    options: [`${prefix}-${i}-correct`, `${prefix}-${i}-wrong`],
    correct_index: 0,
    explanation: `${prefix} explanation ${i}`,
    concept_tag: `${prefix}-concept-${i}`,
    level: i < 2 ? "prerequisite" : i < 4 ? "core" : "stretch",
  }));
}

function decision(over: Partial<DecideResponse>): DecideResponse {
  return {
    action: "REINFORCE",
    band: "REINFORCE",
    next_node: null,
    reached_goal: false,
    gap: "",
    diagnosis: "",
    score_pct: 0.5,
    missed_concepts: [],
    correct_concepts: [],
    ...over,
  };
}

// Answer every MCQ in a quiz by clicking its "-correct" option (one per
// question), so the submit button enables.
function answerAll(prefix: string, count: number) {
  for (let i = 0; i < count; i++) {
    fireEvent.click(screen.getByRole("button", { name: `${prefix}-${i}-correct` }));
  }
}

// After a session starts, the one-time QuestIntro narrative beat now precedes
// the probe (Req 4.1). Dismiss it via "Take up the sword" to reach the probe.
async function beginQuest() {
  fireEvent.click(
    await screen.findByRole("button", { name: "Take up the sword" }),
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  clearActiveGameSessionId();
  localStorage.clear();
  // Default: no branching forks on offer, so the existing single-path loop
  // tests keep the Battle screen's Continue flow. Fork tests override this.
  mockGetPaths.mockResolvedValue([]);
});

afterEach(() => {
  clearActiveGameSessionId();
  localStorage.clear();
});

// --- (a) blank-topic rejection (Req 1.3) -----------------------------------

describe("PlayPage topic entry rejects empty/whitespace topics (Req 1.3)", () => {
  it("rejects an empty topic and does not start a session", async () => {
    render(<PlayPage />);

    // Submitting with the field untouched (empty) must be rejected.
    fireEvent.click(screen.getByRole("button", { name: "Start the quest" }));

    expect(await screen.findByText("Enter a topic to begin.")).toBeInTheDocument();
    expect(mockStart).not.toHaveBeenCalled();
    // No session means we stay on the topic-entry surface.
    expect(
      screen.getByRole("heading", { name: "Name thy quest" }),
    ).toBeInTheDocument();
  });

  it("rejects a whitespace-only topic and does not start a session", async () => {
    render(<PlayPage />);

    const input = screen.getByLabelText("Your topic");
    fireEvent.change(input, { target: { value: "    " } });
    fireEvent.click(screen.getByRole("button", { name: "Start the quest" }));

    expect(await screen.findByText("Enter a topic to begin.")).toBeInTheDocument();
    expect(mockStart).not.toHaveBeenCalled();
  });

  it("clears the error and starts a session once a real topic is entered", async () => {
    mockStart.mockResolvedValue({
      session_id: "s1",
      goal: "Backtracking",
      current_node: "Backtracking",
      probe: questions("probe", 6),
    });

    render(<PlayPage />);

    // First a blank submit to raise the error.
    fireEvent.click(screen.getByRole("button", { name: "Start the quest" }));
    expect(await screen.findByText("Enter a topic to begin.")).toBeInTheDocument();

    // Typing a real topic clears the error and a valid submit starts the session.
    const input = screen.getByLabelText("Your topic");
    fireEvent.change(input, { target: { value: "Backtracking" } });
    expect(screen.queryByText("Enter a topic to begin.")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Start the quest" }));

    await waitFor(() =>
      expect(mockStart).toHaveBeenCalledWith("Backtracking", "test-token"),
    );
    // The QuestIntro beat shows first; dismiss it to reach the probe.
    await beginQuest();
    // We land on the probe phase.
    expect(
      await screen.findByText("Placement probe · Backtracking"),
    ).toBeInTheDocument();
  });
});

// --- (b) full loop transitions (Req 21.1) ----------------------------------

describe("PlayPage drives probe → grading → decision → node → outcome → loop (Req 21.1)", () => {
  it("runs the full state-machine loop end to end", async () => {
    mockStart.mockResolvedValue({
      session_id: "s-loop",
      goal: "Backtracking",
      current_node: "Backtracking",
      probe: questions("probe", 6),
    });
    // First decision (after the probe): DESCEND to a prerequisite node so the
    // loop proceeds into node-delivery rather than ending at the goal.
    mockDecide.mockResolvedValueOnce(
      decision({
        action: "DESCEND",
        band: "DESCEND",
        next_node: "Recursion basics",
        diagnosis: "You stumble on the base case of recursion.",
        gap: "base case",
        score_pct: 0.2,
      }),
    );
    // The delivered node: intuition + checkpoint quiz, no clip (keeps the beat
    // flow at intuition → quiz and avoids the full-screen clip overlay).
    mockDeliver.mockResolvedValue({
      node: "Recursion basics",
      hook: "Recursion is a function that trusts a smaller copy of itself.",
      clip: null,
      quiz: questions("chk", 3),
    });
    // Second decision (after the node checkpoint): CLIMB — this is the loop
    // closing back onto a fresh outcome.
    mockDecide.mockResolvedValueOnce(
      decision({
        action: "CLIMB",
        band: "CLIMB",
        next_node: "Backtracking",
        diagnosis: "You locked in recursion — climb back toward the goal.",
        score_pct: 0.85,
      }),
    );

    render(<PlayPage />);

    // topic-entry → probe.
    fireEvent.change(screen.getByLabelText("Your topic"), {
      target: { value: "Backtracking" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start the quest" }));

    // The QuestIntro beat shows first; dismiss it to reach the probe.
    await beginQuest();

    expect(
      await screen.findByText("Placement probe · Backtracking"),
    ).toBeInTheDocument();

    // probe → grading → decision: answer the 6 probe questions and submit.
    answerAll("probe", 6);
    fireEvent.click(screen.getByRole("button", { name: "Submit placement" }));

    // The DESCEND outcome card appears with its diagnosis (Req 20.4).
    expect(
      await screen.findByText("You stumble on the base case of recursion."),
    ).toBeInTheDocument();
    await waitFor(() => expect(mockDecide).toHaveBeenCalledTimes(1));
    // The decide call carried the session context for banding.
    expect(mockDecide.mock.calls[0][0]).toMatchObject({
      goal: "Backtracking",
      current_node: "Backtracking",
      path: ["Backtracking"],
    });

    // decision → node-delivery: continue from the DESCEND outcome. The retro
    // BattleScreen labels the DESCEND continue action "Retreat & train"
    // (OutcomeCard's "Descend" twin) after the import swap.
    fireEvent.click(screen.getByRole("button", { name: "Retreat & train" }));

    await waitFor(() =>
      expect(mockDeliver).toHaveBeenCalledWith(
        "Recursion basics",
        "Backtracking",
        "test-token",
      ),
    );

    // The node's Intuition_Card renders; advancing the beat reveals the
    // checkpoint quiz (no clip, so intuition → quiz).
    expect(
      await screen.findByText(
        "Recursion is a function that trusts a smaller copy of itself.",
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Take the checkpoint" }));

    expect(
      await screen.findByText("Checkpoint · Recursion basics"),
    ).toBeInTheDocument();

    // node → outcome → loop: answer the 3 checkpoint questions and submit; the
    // second (CLIMB) outcome card closes the loop with a fresh diagnosis.
    answerAll("chk", 3);
    fireEvent.click(screen.getByRole("button", { name: "Submit checkpoint" }));

    expect(
      await screen.findByText(
        "You locked in recursion — climb back toward the goal.",
      ),
    ).toBeInTheDocument();
    await waitFor(() => expect(mockDecide).toHaveBeenCalledTimes(2));
    // The second decide reflects the descended node as the current node.
    expect(mockDecide.mock.calls[1][0]).toMatchObject({
      current_node: "Recursion basics",
    });
  });

  it("surfaces a retry message when starting the session fails and starts no loop", async () => {
    mockStart.mockRejectedValue(new Error("network down"));

    render(<PlayPage />);

    fireEvent.change(screen.getByLabelText("Your topic"), {
      target: { value: "Backtracking" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start the quest" }));

    expect(
      await screen.findByText("We couldn't start that quest. Try again."),
    ).toBeInTheDocument();
    // Stayed on topic-entry; no decide/deliver happened.
    expect(mockDecide).not.toHaveBeenCalled();
    expect(mockDeliver).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Task 6.3 — interaction tests for the reskinned loop (Req 19.1, 19.2, 20.1).
//
// These extend the loop coverage above onto the retro reskin: they assert the
// next-node prefetch still fires during the Battle screen (Property 4), the
// full loop still reaches the goal with the engine's output rendered verbatim
// (Property 2), and the soft empty-quiz "Try again" retry still works. The
// `/api/game/*` engine remains mocked; the real codec runs against jsdom.
// ---------------------------------------------------------------------------

// # Feature: pixel-quest-adventure, Property 4: Prefetch is preserved
describe("PlayPage preserves the next-node prefetch during the Battle screen (Property 4, Req 20.1)", () => {
  it("kicks off the /node prefetch while the Battle screen is shown, before Continue, and reuses it", async () => {
    mockStart.mockResolvedValue({
      session_id: "s-prefetch",
      goal: "Backtracking",
      current_node: "Backtracking",
      probe: questions("probe", 6),
    });
    // The probe resolves to a DESCEND to a prerequisite node — a decision that
    // resolves to a next node, so the prefetch should fire (Property 4).
    mockDecide.mockResolvedValueOnce(
      decision({
        action: "DESCEND",
        band: "DESCEND",
        next_node: "Recursion basics",
        diagnosis: "You stumble on the base case of recursion.",
        gap: "base case",
        score_pct: 0.2,
      }),
    );
    mockDeliver.mockResolvedValue({
      node: "Recursion basics",
      hook: "Recursion is a function that trusts a smaller copy of itself.",
      clip: null,
      quiz: questions("chk", 3),
    });

    render(<PlayPage />);

    fireEvent.change(screen.getByLabelText("Your topic"), {
      target: { value: "Backtracking" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start the quest" }));
    await beginQuest();

    await screen.findByText("Placement probe · Backtracking");
    answerAll("probe", 6);
    fireEvent.click(screen.getByRole("button", { name: "Submit placement" }));

    // The DESCEND Battle screen is on display, with the engine diagnosis. The
    // Continue ("Retreat & train") action has NOT been clicked yet.
    expect(
      await screen.findByText("You stumble on the base case of recursion."),
    ).toBeInTheDocument();

    // Property 4: the next-node prefetch already fired during the Battle screen
    // (before any Continue), so advancing can consume the in-flight result.
    await waitFor(() =>
      expect(mockDeliver).toHaveBeenCalledWith(
        "Recursion basics",
        "Backtracking",
        "test-token",
      ),
    );
    // The prefetch fired while the Battle CTA is still present (pre-Continue).
    expect(
      screen.getByRole("button", { name: "Retreat & train" }),
    ).toBeInTheDocument();

    // Clicking Continue consumes the in-flight prefetch rather than issuing a
    // fresh round-trip — so deliverGameNode is still only called once.
    fireEvent.click(screen.getByRole("button", { name: "Retreat & train" }));
    expect(
      await screen.findByText(
        "Recursion is a function that trusts a smaller copy of itself.",
      ),
    ).toBeInTheDocument();
    expect(mockDeliver).toHaveBeenCalledTimes(1);
  });
});

// # Feature: pixel-quest-adventure, Property 2: The loop reaches the goal identically to the engine
describe("PlayPage loops through node → checkpoint and reaches the goal identically to the engine (Property 2, Req 19.1, 19.2)", () => {
  it("completes topic-entry → QuestIntro (skip) → probe → Battle → node → checkpoint → loop → Dragon falls", async () => {
    mockStart.mockResolvedValue({
      session_id: "s-goal",
      goal: "Backtracking",
      current_node: "Backtracking",
      probe: questions("probe", 6),
    });
    // Loop: DESCEND to a prerequisite, CLIMB back to the goal node, then a final
    // CLIMB that reaches the goal (the Dragon falls). Each decision's output is
    // the engine's — the reskin must render it verbatim, adding/dropping nothing.
    mockDecide
      .mockResolvedValueOnce(
        decision({
          action: "DESCEND",
          band: "DESCEND",
          next_node: "Recursion basics",
          diagnosis: "You stumble on the base case of recursion.",
          gap: "base case",
          score_pct: 0.2,
        }),
      )
      .mockResolvedValueOnce(
        decision({
          action: "CLIMB",
          band: "CLIMB",
          next_node: "Backtracking",
          diagnosis: "Recursion is locked in — climb back toward the Dragon.",
          score_pct: 0.85,
        }),
      )
      .mockResolvedValueOnce(
        decision({
          action: "CLIMB",
          band: "CLIMB",
          next_node: "Backtracking",
          reached_goal: true,
          diagnosis: "You mastered backtracking — the Dragon is slain.",
          score_pct: 0.95,
        }),
      );
    // Deliver returns content keyed by node so it is robust to call order.
    mockDeliver.mockImplementation(async (node: string) => {
      if (node === "Recursion basics") {
        return {
          node,
          hook: "Recursion is a function that trusts a smaller copy of itself.",
          clip: null,
          quiz: questions("chk", 3),
        };
      }
      return {
        node,
        hook: "The Dragon's lair lies just beyond the ridge.",
        clip: null,
        quiz: questions("goalchk", 3),
      };
    });

    render(<PlayPage />);

    // topic-entry → QuestIntro (skip) → probe.
    fireEvent.change(screen.getByLabelText("Your topic"), {
      target: { value: "Backtracking" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start the quest" }));
    await beginQuest();
    await screen.findByText("Placement probe · Backtracking");

    // probe → DESCEND Battle.
    answerAll("probe", 6);
    fireEvent.click(screen.getByRole("button", { name: "Submit placement" }));
    expect(
      await screen.findByText("You stumble on the base case of recursion."),
    ).toBeInTheDocument();

    // Battle → node (prerequisite) → intuition → checkpoint.
    fireEvent.click(screen.getByRole("button", { name: "Retreat & train" }));
    expect(
      await screen.findByText(
        "Recursion is a function that trusts a smaller copy of itself.",
      ),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Take the checkpoint" }));
    await screen.findByText("Checkpoint · Recursion basics");

    // checkpoint → CLIMB Battle (loop back toward the goal node).
    answerAll("chk", 3);
    fireEvent.click(screen.getByRole("button", { name: "Submit checkpoint" }));
    expect(
      await screen.findByText(
        "Recursion is locked in — climb back toward the Dragon.",
      ),
    ).toBeInTheDocument();

    // Advance → goal node delivered → intuition → checkpoint.
    fireEvent.click(screen.getByRole("button", { name: "Advance" }));
    expect(
      await screen.findByText("The Dragon's lair lies just beyond the ridge."),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Take the checkpoint" }));
    await screen.findByText("Checkpoint · Backtracking");

    // checkpoint → final CLIMB that reaches the goal: the Dragon falls and the
    // quest is complete — the engine's goal-reached output rendered verbatim.
    answerAll("goalchk", 3);
    fireEvent.click(screen.getByRole("button", { name: "Submit checkpoint" }));
    expect(await screen.findByText("The Dragon falls!")).toBeInTheDocument();
    expect(
      screen.getByText("You mastered backtracking — the Dragon is slain."),
    ).toBeInTheDocument();
    // Quest-complete: the reach-goal surface offers a fresh quest, not a Continue.
    expect(
      screen.getByRole("button", { name: "Start a new quest" }),
    ).toBeInTheDocument();

    // The loop reached the goal exactly via the engine's three decisions, with
    // the third carrying the goal node as the current node.
    await waitFor(() => expect(mockDecide).toHaveBeenCalledTimes(3));
    expect(mockDecide.mock.calls[2][0]).toMatchObject({
      current_node: "Backtracking",
    });
  });
});

// --- soft empty-quiz checkpoint retry (Req 19.2 / soft-checkpoint contract) --

describe("PlayPage offers a soft retry when a delivered checkpoint quiz is empty", () => {
  it("shows 'Try again' for an empty checkpoint quiz and re-delivers the node on retry", async () => {
    mockStart.mockResolvedValue({
      session_id: "s-empty",
      goal: "Backtracking",
      current_node: "Backtracking",
      probe: questions("probe", 6),
    });
    mockDecide.mockResolvedValueOnce(
      decision({
        action: "DESCEND",
        band: "DESCEND",
        next_node: "Recursion basics",
        diagnosis: "You stumble on the base case of recursion.",
        gap: "base case",
        score_pct: 0.2,
      }),
    );
    // First delivery comes back with an empty quiz (soft checkpoint); the retry
    // re-fetches the node and returns a usable checkpoint quiz.
    mockDeliver
      .mockResolvedValueOnce({
        node: "Recursion basics",
        hook: "Recursion is a function that trusts a smaller copy of itself.",
        clip: null,
        quiz: [],
      })
      .mockResolvedValueOnce({
        node: "Recursion basics",
        hook: "Recursion is a function that trusts a smaller copy of itself.",
        clip: null,
        quiz: questions("chk", 3),
      });

    render(<PlayPage />);

    fireEvent.change(screen.getByLabelText("Your topic"), {
      target: { value: "Backtracking" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start the quest" }));
    await beginQuest();
    await screen.findByText("Placement probe · Backtracking");

    answerAll("probe", 6);
    fireEvent.click(screen.getByRole("button", { name: "Submit placement" }));
    await screen.findByText("You stumble on the base case of recursion.");

    // Battle → node → intuition → checkpoint beat (empty quiz).
    fireEvent.click(screen.getByRole("button", { name: "Retreat & train" }));
    await screen.findByText(
      "Recursion is a function that trusts a smaller copy of itself.",
    );
    fireEvent.click(screen.getByRole("button", { name: "Take the checkpoint" }));

    // Soft checkpoint: an empty quiz never hard-blocks — it offers a retry.
    expect(
      await screen.findByText(
        "We couldn't build a checkpoint for this node just now.",
      ),
    ).toBeInTheDocument();

    // Retrying re-delivers the node: delivery restarts at the intuition beat,
    // and advancing then reveals the usable checkpoint quiz.
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(mockDeliver).toHaveBeenCalledTimes(2));
    fireEvent.click(
      await screen.findByRole("button", { name: "Take the checkpoint" }),
    );
    expect(
      await screen.findByText("Checkpoint · Recursion basics"),
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Task 9.3 — learner-chosen branching forks (Req 14.1, 14.2, 14.3).
//
// At a (non-goal) decision the Play_Surface asks `/api/game/paths` for
// alternative on-goal next Stages. With two+ candidates a fork is offered on the
// World_Map (Req 14.1) and picking one advances the loop along that Stage via
// the normal decide/node flow (Req 14.2). With fewer than two (or an
// empty/unavailable endpoint) the surface falls back to the single
// engine-chosen node behind the Battle screen's Continue (Req 14.3).
// ---------------------------------------------------------------------------

describe("PlayPage renders branching forks and advances on selection (Req 14.1, 14.2, 14.3)", () => {
  it("offers the candidate Stages at a decision and advancing a fork delivers the chosen node", async () => {
    mockStart.mockResolvedValue({
      session_id: "s-fork",
      goal: "Backtracking",
      current_node: "Backtracking",
      probe: questions("probe", 6),
    });
    // The probe resolves to a DESCEND with a recommended prerequisite. Because
    // it is a non-goal decision with a next node, the fork lookup fires.
    mockDecide.mockResolvedValueOnce(
      decision({
        action: "DESCEND",
        band: "DESCEND",
        next_node: "Recursion basics",
        diagnosis: "You stumble on the base case of recursion.",
        gap: "base case",
        score_pct: 0.2,
      }),
    );
    // Two candidates → a real fork (Req 14.1). The learner-chosen Stage differs
    // from the engine's recommended `next_node`.
    mockGetPaths.mockResolvedValue(["Recursion basics", "Trees basics"]);
    // Deliver keyed by node so the chosen fork's content is returned.
    mockDeliver.mockImplementation(async (node: string) => ({
      node,
      hook:
        node === "Trees basics"
          ? "A tree is recursion you can see."
          : "Recursion is a function that trusts a smaller copy of itself.",
      clip: null,
      quiz: questions("chk", 3),
    }));

    render(<PlayPage />);

    fireEvent.change(screen.getByLabelText("Your topic"), {
      target: { value: "Backtracking" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start the quest" }));
    await beginQuest();
    await screen.findByText("Placement probe · Backtracking");

    answerAll("probe", 6);
    fireEvent.click(screen.getByRole("button", { name: "Submit placement" }));

    // The DESCEND Battle screen is shown; the fork lookup fires for this
    // non-goal decision with the session context (Req 14.1).
    await screen.findByText("You stumble on the base case of recursion.");
    await waitFor(() =>
      expect(mockGetPaths).toHaveBeenCalledWith(
        "Backtracking",
        "Backtracking",
        ["Backtracking"],
        "test-token",
      ),
    );

    // With two candidates the fork is active: the hint appears and both
    // candidate Stages render as selectable buttons on the World_Map.
    expect(await screen.findByText(/The trail forks/)).toBeInTheDocument();
    const chosen = await screen.findByRole("button", { name: /Trees basics/ });
    expect(
      screen.getByRole("button", { name: /Recursion basics/ }),
    ).toBeInTheDocument();

    // Selecting a fork advances the loop along the chosen Stage (Req 14.2):
    // the chosen node is delivered via the normal node-delivery flow.
    fireEvent.click(chosen);
    await waitFor(() =>
      expect(mockDeliver).toHaveBeenCalledWith(
        "Trees basics",
        "Backtracking",
        "test-token",
      ),
    );
    // The chosen node's content renders and the fork hint is gone.
    expect(
      await screen.findByText("A tree is recursion you can see."),
    ).toBeInTheDocument();
    expect(screen.queryByText(/The trail forks/)).toBeNull();
  });

  it("falls back to the single engine-chosen node when no candidates are offered (Req 14.3)", async () => {
    mockStart.mockResolvedValue({
      session_id: "s-nofork",
      goal: "Backtracking",
      current_node: "Backtracking",
      probe: questions("probe", 6),
    });
    mockDecide.mockResolvedValueOnce(
      decision({
        action: "DESCEND",
        band: "DESCEND",
        next_node: "Recursion basics",
        diagnosis: "You stumble on the base case of recursion.",
        gap: "base case",
        score_pct: 0.2,
      }),
    );
    // No candidates → no fork; the loop stays on the single-path flow (Req 14.3).
    mockGetPaths.mockResolvedValue([]);
    mockDeliver.mockResolvedValue({
      node: "Recursion basics",
      hook: "Recursion is a function that trusts a smaller copy of itself.",
      clip: null,
      quiz: questions("chk", 3),
    });

    render(<PlayPage />);

    fireEvent.change(screen.getByLabelText("Your topic"), {
      target: { value: "Backtracking" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start the quest" }));
    await beginQuest();
    await screen.findByText("Placement probe · Backtracking");

    answerAll("probe", 6);
    fireEvent.click(screen.getByRole("button", { name: "Submit placement" }));

    await screen.findByText("You stumble on the base case of recursion.");
    // The endpoint was consulted but returned nothing actionable.
    await waitFor(() => expect(mockGetPaths).toHaveBeenCalled());
    // No fork hint and no selectable candidate Stage — single-path fallback.
    expect(screen.queryByText(/The trail forks/)).toBeNull();
    expect(screen.queryByRole("button", { name: /Trees basics/ })).toBeNull();

    // Continuing from the Battle screen advances to the engine-chosen node.
    fireEvent.click(screen.getByRole("button", { name: "Retreat & train" }));
    await waitFor(() =>
      expect(mockDeliver).toHaveBeenCalledWith(
        "Recursion basics",
        "Backtracking",
        "test-token",
      ),
    );
    expect(
      await screen.findByText(
        "Recursion is a function that trusts a smaller copy of itself.",
      ),
    ).toBeInTheDocument();
  });

  it("does not offer a fork when only one candidate is returned (needs two+, Req 14.1)", async () => {
    mockStart.mockResolvedValue({
      session_id: "s-onefork",
      goal: "Backtracking",
      current_node: "Backtracking",
      probe: questions("probe", 6),
    });
    mockDecide.mockResolvedValueOnce(
      decision({
        action: "DESCEND",
        band: "DESCEND",
        next_node: "Recursion basics",
        diagnosis: "You stumble on the base case of recursion.",
        gap: "base case",
        score_pct: 0.2,
      }),
    );
    // A single candidate is not a real fork — the learner is never blocked into
    // a one-option choice; the single-path flow stands (Req 14.1, 14.3).
    mockGetPaths.mockResolvedValue(["Recursion basics"]);
    mockDeliver.mockResolvedValue({
      node: "Recursion basics",
      hook: "Recursion is a function that trusts a smaller copy of itself.",
      clip: null,
      quiz: questions("chk", 3),
    });

    render(<PlayPage />);

    fireEvent.change(screen.getByLabelText("Your topic"), {
      target: { value: "Backtracking" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start the quest" }));
    await beginQuest();
    await screen.findByText("Placement probe · Backtracking");

    answerAll("probe", 6);
    fireEvent.click(screen.getByRole("button", { name: "Submit placement" }));

    await screen.findByText("You stumble on the base case of recursion.");
    await waitFor(() => expect(mockGetPaths).toHaveBeenCalled());
    // Single candidate → no fork hint; Continue still drives the engine route.
    expect(screen.queryByText(/The trail forks/)).toBeNull();
    expect(
      screen.getByRole("button", { name: "Retreat & train" }),
    ).toBeInTheDocument();
  });
});
