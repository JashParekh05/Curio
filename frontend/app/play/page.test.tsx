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
}));

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ session: { access_token: "test-token" } }),
}));

import PlayPage from "./page";
import { startGameSession, decideGame, deliverGameNode } from "@/lib/api";
import {
  clearActiveGameSessionId,
  type ProbeQuestion,
  type DecideResponse,
} from "@/lib/game-progress";

const mockStart = vi.mocked(startGameSession);
const mockDecide = vi.mocked(decideGame);
const mockDeliver = vi.mocked(deliverGameNode);

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

beforeEach(() => {
  vi.clearAllMocks();
  clearActiveGameSessionId();
  localStorage.clear();
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
      screen.getByRole("heading", { name: "What do you want to learn?" }),
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

    // decision → node-delivery: continue from the DESCEND outcome.
    fireEvent.click(screen.getByRole("button", { name: "Descend" }));

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
