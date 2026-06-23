import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import SkillTreeMap from "./SkillTreeMap";
import { type NodeState, type NodeView } from "@/lib/game-progress";

// Component tests for the Skill_Tree_Map (Task 9.6, Req 19.3).
//
// Req 19.3: "render each Node in exactly one Node_State from the set LOCKED,
// NEXT, CLEARED, DECAYING, MASTERED." These tests pin (a) that every rendered
// tile carries exactly one state label and exactly one accent fill, and (b)
// that the accent fill matches the documented mapping (Req 23.2):
//   lime = MASTERED/CLEARED, cyan = NEXT, orange = DECAYING,
//   ink/40 (bg-white/40) = LOCKED, pink = the active marker (overlay, not a
//   tile state).

// Documented accent fill per node state (Req 19.3 + 23.2).
const ACCENT_BY_STATE: Record<NodeState, string> = {
  MASTERED: "bg-accent-lime",
  CLEARED: "bg-accent-lime",
  NEXT: "bg-accent-cyan",
  DECAYING: "bg-accent-orange",
  LOCKED: "bg-white/40",
};

// Documented learner-facing label per node state.
const LABEL_BY_STATE: Record<NodeState, string> = {
  MASTERED: "Mastered",
  CLEARED: "Cleared",
  NEXT: "Next battle",
  DECAYING: "Decaying",
  LOCKED: "Locked",
};

// The full set of tile accent fills and labels a node could possibly take. Used
// to prove a tile carries *exactly one* of each (the "exactly one Node_State"
// guarantee of Req 19.3).
const ALL_ACCENTS = [
  "bg-accent-lime",
  "bg-accent-cyan",
  "bg-accent-orange",
  "bg-white/40",
];
const ALL_LABELS = ["Mastered", "Cleared", "Next battle", "Decaying", "Locked"];

// One node in each of the five states, plus a goal node, so a single render
// exercises the whole Node_State set. Node names are deliberately neutral (no
// state-label words) so a label match can never be confused with a node name.
const NODES: Record<string, NodeView> = {
  Recursion: { node: "Recursion", state: "NEXT", is_goal: true },
  Arrays: { node: "Arrays", state: "MASTERED", is_goal: false },
  Pointers: { node: "Pointers", state: "CLEARED", is_goal: false },
  Graphs: { node: "Graphs", state: "DECAYING", is_goal: false },
  Trees: { node: "Trees", state: "LOCKED", is_goal: false },
};

// Resolve the tile element (the `.brutal-card` chrome) that renders a node by
// locating the node-name text and climbing to its enclosing tile.
function tileFor(name: string): HTMLElement {
  const tile = screen.getByText(name).closest(".brutal-card") as HTMLElement;
  expect(tile).not.toBeNull();
  return tile;
}

// Count how many of the documented accent fills appear on a tile.
function accentsOn(tile: HTMLElement): string[] {
  return ALL_ACCENTS.filter((a) => tile.className.includes(a));
}

// Count how many of the documented state labels are rendered within a tile.
function labelsOn(tile: HTMLElement): string[] {
  return ALL_LABELS.filter(
    (label) => within(tile).queryAllByText(new RegExp(label)).length > 0,
  );
}

describe("SkillTreeMap renders each node in exactly one Node_State (Req 19.3)", () => {
  it.each(Object.entries(NODES))(
    "renders %s with exactly one state label and one accent fill",
    (name, view) => {
      render(
        <SkillTreeMap
          nodes={NODES}
          goal="Recursion"
          currentNode="Pointers"
          floorNode={null}
        />,
      );

      const tile = tileFor(name);

      // Exactly one accent fill, and it is the documented one for this state.
      expect(accentsOn(tile)).toEqual([ACCENT_BY_STATE[view.state]]);

      // Exactly one state label, and it is the documented one for this state.
      const labels = labelsOn(tile);
      expect(labels).toHaveLength(1);
      expect(labels[0]).toContain(LABEL_BY_STATE[view.state]);
    },
  );

  it("renders every discovered node as a single tile", () => {
    render(
      <SkillTreeMap
        nodes={NODES}
        goal="Recursion"
        currentNode="Pointers"
        floorNode={null}
      />,
    );
    // One tile per node, no node rendered twice.
    Object.keys(NODES).forEach((name) => {
      expect(screen.getAllByText(name)).toHaveLength(1);
    });
  });
});

describe("SkillTreeMap marker and floor overlays (Req 23.2 pink marker, Req 6.4)", () => {
  it("places the pink 'You are here' marker without changing the tile's state accent", () => {
    render(
      <SkillTreeMap
        nodes={NODES}
        goal="Recursion"
        currentNode="Pointers"
        floorNode={null}
      />,
    );

    // The marker (pink) is an overlay distinct from the tile's state fill.
    const marker = screen.getByLabelText("You are here");
    expect(marker.className).toContain("bg-accent-pink");

    // The current node keeps its CLEARED (lime) fill — pink is not a tile state.
    const currentTile = tileFor("Pointers");
    expect(accentsOn(currentTile)).toEqual(["bg-accent-lime"]);
    expect(currentTile.className).not.toContain("bg-accent-pink");
  });

  it("shows the 'You start here' indication at the Floor_Node", () => {
    render(
      <SkillTreeMap
        nodes={NODES}
        goal="Recursion"
        currentNode="Pointers"
        floorNode="Trees"
      />,
    );
    expect(screen.getByText("You start here")).toBeInTheDocument();
  });
});

describe("SkillTreeMap updates a node's rendered Node_State on change (Req 19.4)", () => {
  it("re-renders the tile in its new state when the node's state changes", () => {
    const { rerender } = render(
      <SkillTreeMap
        nodes={NODES}
        goal="Recursion"
        currentNode="Pointers"
        floorNode={null}
      />,
    );

    // Initially LOCKED.
    let lockedTile = tileFor("Trees");
    expect(accentsOn(lockedTile)).toEqual(["bg-white/40"]);
    expect(labelsOn(lockedTile)).toEqual(["Locked"]);

    // The node advances to NEXT.
    const updated: Record<string, NodeView> = {
      ...NODES,
      Trees: { node: "Trees", state: "NEXT", is_goal: false },
    };
    rerender(
      <SkillTreeMap
        nodes={updated}
        goal="Recursion"
        currentNode="Pointers"
        floorNode={null}
      />,
    );

    const advancedTile = tileFor("Trees");
    expect(accentsOn(advancedTile)).toEqual(["bg-accent-cyan"]);
    expect(labelsOn(advancedTile)).toEqual(["Next battle"]);
  });
});
