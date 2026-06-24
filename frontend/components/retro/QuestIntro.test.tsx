// Component tests for `QuestIntro` — the opening narrative beat
// (Task 5.4, Req 4.1, 4.2, 4.4).
//
// Guarantees pinned here:
//
//   1. Names the goal (Req 4.1, 4.2) — the intro frames the entered topic as
//      the Dragon, so the narrative names the actual subject the learner typed.
//   2. Skippable (Req 4.4) — the Skip affordance fires `onSkip` so a returning
//      learner is never blocked; Begin fires `onBegin`.

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";

import QuestIntro from "./QuestIntro";

afterEach(() => {
  cleanup();
});

describe("QuestIntro names the goal (Req 4.1, 4.2)", () => {
  it("frames the entered topic as the Dragon by name", () => {
    render(
      <QuestIntro goal="recursion" onBegin={() => {}} onSkip={() => {}} />,
    );

    // The title names the actual subject as the Dragon.
    expect(screen.getByText(/The Dragon of recursion/i)).toBeInTheDocument();

    // The narrative references the topic in its body copy too.
    const region = screen.getByLabelText("Quest introduction");
    expect(region.textContent ?? "").toMatch(/recursion/);
  });

  it("reflects a different topic verbatim", () => {
    render(
      <QuestIntro
        goal="quantum entanglement"
        onBegin={() => {}}
        onSkip={() => {}}
      />,
    );
    expect(
      screen.getByText(/The Dragon of quantum entanglement/i),
    ).toBeInTheDocument();
  });
});

describe("QuestIntro is skippable (Req 4.4)", () => {
  it("fires onSkip when the Skip button is clicked", () => {
    const onSkip = vi.fn();
    const onBegin = vi.fn();
    render(<QuestIntro goal="recursion" onBegin={onBegin} onSkip={onSkip} />);

    fireEvent.click(screen.getByRole("button", { name: /skip/i }));

    expect(onSkip).toHaveBeenCalledTimes(1);
    expect(onBegin).not.toHaveBeenCalled();
  });

  it("fires onBegin when the begin button is clicked", () => {
    const onSkip = vi.fn();
    const onBegin = vi.fn();
    render(<QuestIntro goal="recursion" onBegin={onBegin} onSkip={onSkip} />);

    fireEvent.click(screen.getByRole("button", { name: /take up the sword/i }));

    expect(onBegin).toHaveBeenCalledTimes(1);
    expect(onSkip).not.toHaveBeenCalled();
  });
});
