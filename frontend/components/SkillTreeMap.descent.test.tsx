import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, within, act } from "@testing-library/react";
import SkillTreeMap, { DESCENT_STEP_MS } from "./SkillTreeMap";
import { type NodeView } from "@/lib/game-progress";

// Interaction test for the descent animation (Task 15.2, Req 18.1, 18.2).
//
// Req 18.1: "WHEN a probe results in a descent, THE Play_Surface SHALL animate
// the marker walking DOWN the Skill_Tree_Map one Node at a time until it lands
// on the Floor_Node."
// Req 18.2: "WHEN the descent animation completes, THE Play_Surface SHALL
// display the 'You start here' indication at the Floor_Node."
//
// The descent walk is driven by the `animateDescentTo` prop, ticks one tile per
// DESCENT_STEP_MS, exposes the marker as "Walking down…" while in flight, and
// withholds the "You start here" indication (and fires `onDescentComplete`) only
// once the marker lands on the Floor_Node. We drive it deterministically with
// fake timers stepping DESCENT_STEP_MS at a time.

// Goal (boss keep, top of map) plus a descended prerequisite chain. Node names
// are neutral so a name match is never confused with a label/indication.
const NODES: Record<string, NodeView> = {
  Recursion: { node: "Recursion", state: "CLEARED", is_goal: true },
  Induction: { node: "Induction", state: "CLEARED", is_goal: false },
  Functions: { node: "Functions", state: "NEXT", is_goal: false },
};

// Top→bottom order the component renders: goal first, then path order. The
// Floor_Node sits lower in the path so the marker has tiles to walk through.
const PATH = ["Recursion", "Induction", "Functions"];
const FLOOR = "Functions"; // index 2 in the ordering → two walk steps

// The <li> tile the walking marker currently sits on. The marker div (labelled
// "Walking down") renders inside the same <li> as its node's tile.
function walkingMarkerTile(): HTMLElement {
  const marker = screen.getByLabelText("Walking down");
  const li = marker.closest("li") as HTMLElement;
  expect(li).not.toBeNull();
  return li;
}

describe("SkillTreeMap descent animation (Req 18.1, 18.2)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("walks the marker down node-by-node and withholds 'You start here' until it lands", () => {
    const onDescentComplete = vi.fn();

    render(
      <SkillTreeMap
        nodes={NODES}
        goal="Recursion"
        currentNode="Recursion"
        floorNode={null}
        animateDescentTo={FLOOR}
        onDescentComplete={onDescentComplete}
        path={PATH}
      />,
    );

    // Step 0: the marker starts at the top (the boss keep) and is walking.
    expect(within(walkingMarkerTile()).getByText("Recursion")).toBeInTheDocument();
    // The floor indication is withheld and the walk has not completed yet.
    expect(screen.queryByText("You start here")).not.toBeInTheDocument();
    expect(onDescentComplete).not.toHaveBeenCalled();

    // Step 1: after one tick the marker has walked DOWN to the next node.
    act(() => {
      vi.advanceTimersByTime(DESCENT_STEP_MS);
    });
    expect(within(walkingMarkerTile()).getByText("Induction")).toBeInTheDocument();
    // Still walking — floor indication remains withheld (Req 18.2).
    expect(screen.queryByText("You start here")).not.toBeInTheDocument();
    expect(onDescentComplete).not.toHaveBeenCalled();

    // Step 2: the next tick lands the marker on the Floor_Node.
    act(() => {
      vi.advanceTimersByTime(DESCENT_STEP_MS);
    });

    // The walk is done: the marker is no longer "Walking down" and now reads as
    // "You are here" on the Floor_Node.
    expect(screen.queryByLabelText("Walking down")).not.toBeInTheDocument();
    const landed = screen.getByLabelText("You are here").closest("li") as HTMLElement;
    expect(within(landed).getByText("Functions")).toBeInTheDocument();

    // The "You start here" indication appears on completion, and the completion
    // callback fires exactly once (Req 18.2).
    expect(screen.getByText("You start here")).toBeInTheDocument();
    expect(onDescentComplete).toHaveBeenCalledTimes(1);
  });

  it("lands immediately (no step-by-step walk) when reduced motion is preferred", () => {
    // Mock matchMedia so prefers-reduced-motion: reduce matches.
    const matchMedia = vi.fn().mockReturnValue({
      matches: true,
      media: "(prefers-reduced-motion: reduce)",
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
      onchange: null,
    });
    vi.stubGlobal("matchMedia", matchMedia);

    const onDescentComplete = vi.fn();

    render(
      <SkillTreeMap
        nodes={NODES}
        goal="Recursion"
        currentNode="Recursion"
        floorNode={null}
        animateDescentTo={FLOOR}
        onDescentComplete={onDescentComplete}
        path={PATH}
      />,
    );

    // No intermediate "Walking down" beat — the marker lands on the floor at once.
    expect(screen.queryByLabelText("Walking down")).not.toBeInTheDocument();
    const landed = screen.getByLabelText("You are here").closest("li") as HTMLElement;
    expect(within(landed).getByText("Functions")).toBeInTheDocument();

    // The floor indication is shown and the callback fired without any timers.
    expect(screen.getByText("You start here")).toBeInTheDocument();
    expect(onDescentComplete).toHaveBeenCalledTimes(1);

    vi.unstubAllGlobals();
  });
});
