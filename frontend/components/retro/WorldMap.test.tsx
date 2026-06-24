// # Feature: pixel-quest-adventure, Property 1: Stage state is a total 1:1 map of Node_State
//
// Component / property test for `WorldMap` — the retro reskin of `SkillTreeMap`
// (Task 2.4, Req 3.3, 3.4, 3.6). Ported from `SkillTreeMap.test.tsx`, adapted to
// WorldMap's overworld rendering (sprite + `--pq-*` tint per Stage_State, the
// Dragon's Keep goal, the "You start here" camp, and the statically-placed
// Knight under reduced motion).
//
// Property 1 (Req 3.3, 11.1): the Stage_State → sprite/tint mapping is TOTAL and
// 1:1 with `NodeState`. For every `NodeView.state` in {LOCKED, NEXT, CLEARED,
// DECAYING, MASTERED} a Stage renders exactly one sprite, exactly one state
// label, and exactly one documented tint; the Goal_Node renders as the Dragon's
// Keep — every state maps to one and only one visual, none left unrendered.

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import WorldMap from "./WorldMap";
import { type NodeState, type NodeView } from "@/lib/game-progress";

// Documented sprite per Stage_State (mirrors `stageSprite` in WorldMap.tsx).
const SPRITE_BY_STATE: Record<NodeState, string> = {
  MASTERED: "🏆", // slain-monster trophy
  CLEARED: "🚩", // cleared camp / planted flag
  NEXT: "⚔️", // live monster lair
  DECAYING: "🔥", // re-spawning ember
  LOCKED: "🌫️", // fogged tile
};

// Documented `--pq-*` tint (background fill) per Stage_State.
const TINT_BY_STATE: Record<NodeState, string> = {
  MASTERED: "var(--pq-gold)",
  CLEARED: "var(--pq-lime)",
  NEXT: "var(--pq-cyan)",
  DECAYING: "var(--pq-orange)",
  LOCKED: "var(--pq-locked)",
};

// Documented learner-facing state label (mirrors `stateLabel`).
const LABEL_BY_STATE: Record<NodeState, string> = {
  MASTERED: "Mastered",
  CLEARED: "Cleared",
  NEXT: "Next battle",
  DECAYING: "Decaying",
  LOCKED: "Locked",
};

// The full set of state sprites / labels a Stage could possibly take. Used to
// prove a Stage carries *exactly one* of each (the "exactly one Stage_State"
// guarantee of Req 3.3).
const ALL_SPRITES = ["🏆", "🚩", "⚔️", "🔥", "🌫️"];
const ALL_LABELS = ["Mastered", "Cleared", "Next battle", "Decaying", "Locked"];

// The goal's Dragon's Keep treatment (rendered separately from the state set).
const GOAL_SPRITE = "🐉";
const GOAL_TINT = "var(--pq-red)";

// One NON-goal node in each of the five states, plus a separate Goal_Node, so a
// single render exercises the whole Node_State set independently of the goal.
// Names are neutral (no state-label words) so a label match can never be
// confused with a node name.
const NODES: Record<string, NodeView> = {
  Dragon: { node: "Dragon", state: "NEXT", is_goal: true },
  Arrays: { node: "Arrays", state: "MASTERED", is_goal: false },
  Pointers: { node: "Pointers", state: "CLEARED", is_goal: false },
  Graphs: { node: "Graphs", state: "DECAYING", is_goal: false },
  Trees: { node: "Trees", state: "LOCKED", is_goal: false },
  Loops: { node: "Loops", state: "NEXT", is_goal: false },
};

// Every non-goal Stage and its expected state, for table-driven assertions.
const NON_GOAL = Object.entries(NODES).filter(([, v]) => !v.is_goal) as [
  string,
  NodeView,
][];

// Resolve the Stage tile (the `StageTile` chrome) rendering a node by locating
// the node-name text and climbing to the nearest ancestor carrying a tint
// `background` fill. Only the StageTile sets a solid `var(--pq-*)` background;
// the wrapping layers carry none and the map root uses a gradient, so the first
// ancestor with a non-empty `background` is exactly this node's tile.
function tileFor(name: string): HTMLElement {
  let el: HTMLElement | null = screen.getByText(name);
  while (el && !el.style.background) {
    el = el.parentElement;
  }
  expect(el).not.toBeNull();
  return el as HTMLElement;
}

// Which documented state sprites appear within a tile.
function spritesOn(tile: HTMLElement): string[] {
  const text = tile.textContent ?? "";
  return ALL_SPRITES.filter((s) => text.includes(s));
}

// Which documented state labels appear within a tile.
function labelsOn(tile: HTMLElement): string[] {
  const text = tile.textContent ?? "";
  return ALL_LABELS.filter((l) => text.includes(l));
}

// Build a matchMedia stub whose `(prefers-reduced-motion: reduce)` query reports
// `matches`. Shaped like the RetroThemeProvider test's stub.
function stubMatchMedia(matches: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockReturnValue({
      matches,
      media: "(prefers-reduced-motion: reduce)",
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
      onchange: null,
    }),
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Property 1: WorldMap renders each Stage in exactly one Stage_State (Req 3.3)", () => {
  it.each(NON_GOAL)(
    "renders %s with exactly one state sprite, label, and the documented tint",
    (name, view) => {
      render(
        <WorldMap
          nodes={NODES}
          goal="Dragon"
          currentNode="Pointers"
          floorNode={null}
        />,
      );

      const tile = tileFor(name);

      // Exactly one state sprite, and it is the documented one for this state.
      expect(spritesOn(tile)).toEqual([SPRITE_BY_STATE[view.state]]);

      // Exactly one state label, and it is the documented one for this state.
      expect(labelsOn(tile)).toEqual([LABEL_BY_STATE[view.state]]);

      // Exactly one tint, the documented `--pq-*` fill for this state.
      expect(tile.style.background).toBe(TINT_BY_STATE[view.state]);
    },
  );

  it("renders every discovered node as a single tile", () => {
    render(
      <WorldMap
        nodes={NODES}
        goal="Dragon"
        currentNode="Pointers"
        floorNode={null}
      />,
    );
    Object.keys(NODES).forEach((name) => {
      expect(screen.getAllByText(name)).toHaveLength(1);
    });
  });
});

describe("WorldMap renders the Goal_Node as the Dragon's Keep (Req 3.2)", () => {
  it("draws the goal with the dragon sprite, keep banner, red tint, and no plain state treatment", () => {
    render(
      <WorldMap
        nodes={NODES}
        goal="Dragon"
        currentNode="Pointers"
        floorNode={null}
      />,
    );

    const goalTile = tileFor("Dragon");

    // The Keep castle banner and the dragon boss sprite mark the destination.
    expect(goalTile.textContent).toContain("Dragon's Keep");
    expect(goalTile.textContent).toContain(GOAL_SPRITE);
    expect(goalTile.textContent).toContain("Goal · Dragon");

    // Red tint — distinct from any state tint.
    expect(goalTile.style.background).toBe(GOAL_TINT);

    // The goal does NOT render a plain Stage_State sprite or label, even though
    // its underlying NodeView.state is NEXT — the Dragon treatment is total.
    expect(spritesOn(goalTile)).toEqual([]);
    expect(labelsOn(goalTile)).toEqual([]);
  });
});

describe("WorldMap updates a Stage's rendered Stage_State on change (Req 3.4)", () => {
  it("re-renders the tile in its new state when the node's state changes", () => {
    const { rerender } = render(
      <WorldMap
        nodes={NODES}
        goal="Dragon"
        currentNode="Pointers"
        floorNode={null}
      />,
    );

    // Initially LOCKED.
    const lockedTile = tileFor("Trees");
    expect(spritesOn(lockedTile)).toEqual([SPRITE_BY_STATE.LOCKED]);
    expect(labelsOn(lockedTile)).toEqual(["Locked"]);
    expect(lockedTile.style.background).toBe(TINT_BY_STATE.LOCKED);

    // The node advances to NEXT.
    const updated: Record<string, NodeView> = {
      ...NODES,
      Trees: { node: "Trees", state: "NEXT", is_goal: false },
    };
    rerender(
      <WorldMap
        nodes={updated}
        goal="Dragon"
        currentNode="Pointers"
        floorNode={null}
      />,
    );

    const advancedTile = tileFor("Trees");
    expect(spritesOn(advancedTile)).toEqual([SPRITE_BY_STATE.NEXT]);
    expect(labelsOn(advancedTile)).toEqual(["Next battle"]);
    expect(advancedTile.style.background).toBe(TINT_BY_STATE.NEXT);
  });
});

describe("WorldMap floor camp (Req 3.6)", () => {
  it("shows the 'You start here' camp at the Floor_Node", () => {
    render(
      <WorldMap
        nodes={NODES}
        goal="Dragon"
        currentNode="Pointers"
        floorNode="Trees"
      />,
    );
    expect(screen.getByText(/You start here/)).toBeInTheDocument();
  });
});

describe("WorldMap reduced motion places the Knight statically (Req 1.4, 2.3)", () => {
  it("lands the Knight at the descent target idle (no walk) and completes immediately", () => {
    stubMatchMedia(true); // prefers-reduced-motion: reduce
    const onDescentComplete = vi.fn();

    render(
      <WorldMap
        nodes={NODES}
        goal="Dragon"
        currentNode="Pointers"
        floorNode="Trees"
        animateDescentTo="Trees"
        onDescentComplete={onDescentComplete}
      />,
    );

    // The descent resolves to its end-state immediately under reduced motion —
    // no step-by-step walk.
    expect(onDescentComplete).toHaveBeenCalledTimes(1);

    // The Knight is placed statically: the idle ("Knight is here") sprite, never
    // the walking sprite, and its anchor reports it is not walking.
    expect(screen.getByLabelText("Knight is here")).toBeInTheDocument();
    expect(screen.queryByLabelText("Knight walking")).toBeNull();

    const anchor = document.querySelector(
      "[data-knight-anchor]",
    ) as HTMLElement;
    expect(anchor).not.toBeNull();
    expect(anchor.getAttribute("data-walking")).toBe("false");
  });
});
