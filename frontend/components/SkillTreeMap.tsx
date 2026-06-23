"use client";

// Skill_Tree_Map — the home surface of the Play_Surface (Req 19, 23.2, 23.3).
//
// Two visual layers compose here, per the design's "Medieval pixel-art adventure
// UX": a pixel-art medieval map sits underneath as a background/sprite layer
// (grass tiles, a winding path, a boss keep at the top, a little adventurer
// marker), and the interactive chrome on top stays brutalist — `.brutal-card`
// tiles, thick ink borders, flat accent fills — so the pixel art reads as the
// "world" and the brutalist tokens read as the "UI on top of the world".
//
// The Goal_Node is the boss keep at the top (Req 19.2). Each discovered node is
// rendered in exactly one Node_State (Req 19.3) with the documented accent
// mapping (Req 23.2): lime = MASTERED/CLEARED, cyan = NEXT, orange = DECAYING,
// ink/40 = LOCKED, pink = the active marker. The descended prerequisite chain
// and the climb back toward the goal are both drawn (Req 19.1), and the
// Floor_Node carries a "You start here" indication (Req 6.4). State is derived
// purely from props, so a node's rendered state updates whenever its state
// changes (Req 19.4).
//
// DESCENT ANIMATION (Phase 2, task 15.1, Req 18): on a descent the marker walks
// DOWN the map one node at a time — from the boss keep at the top down through
// the descended prerequisite chain — until it lands on the Floor_Node, and only
// THEN does the "You start here" indication appear (Req 18.1, 18.2). The walk is
// driven by the optional `animateDescentTo` prop; an internal step effect moves
// the marker through the intermediate tiles. Respecting the user's motion
// preference, `prefers-reduced-motion: reduce` skips the step-by-step walk and
// lands the marker on the floor immediately. When `animateDescentTo` is omitted
// the component is fully backward compatible: the marker is placed statically at
// `currentNode` and the floor indication is driven by the `floorNode` prop.

import { useEffect, useState } from "react";

import { type NodeState, type NodeView } from "@/lib/game-progress";

// Milliseconds the marker rests on each tile while walking down the map. Exported
// so an interaction test can drive the animation deterministically (task 15.2).
export const DESCENT_STEP_MS = 450;

// True when the user has asked the OS to reduce motion. SSR-guarded and wrapped
// so an environment without `matchMedia` never throws (the walk simply runs).
function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

export interface SkillTreeMapProps {
  // Discovered nodes for the map, keyed by node name (mirrors
  // GameSessionState.nodes). Each renders as a map tile in its single state.
  nodes: Record<string, NodeView>;
  // The Goal_Node (entered topic) — rendered as the boss keep at the top.
  goal: string;
  // The node the adventurer marker currently sits on.
  currentNode: string;
  // The Floor_Node ("You start here"), or null before a floor is established.
  floorNode: string | null;
  // PHASE 2 descent animation (Req 18). When set, the marker walks DOWN the map
  // node-by-node from the top until it lands on this node (the established
  // Floor_Node), and the "You start here" indication is withheld until the walk
  // completes. When omitted/null the marker is placed statically at `currentNode`
  // and the floor indication follows `floorNode` (backward compatible).
  animateDescentTo?: string | null;
  // Optional callback fired once the descent walk reaches the Floor_Node (also
  // fired immediately when reduced motion is preferred). Useful for sequencing
  // the outcome card after the signature descent moment, and for tests.
  onDescentComplete?: () => void;
  // Ordered visited nodes (descent chain + climb). Used to order tiles
  // top→bottom (goal at top, floor at bottom). Optional: when omitted, the map
  // falls back to the discovered-node keys with the goal pinned to the top.
  path?: string[];
  // Optional click handler for a tile (e.g. to inspect a node). Non-interactive
  // when omitted.
  onSelectNode?: (node: string) => void;
}

// Per-state brutalist accent fill for a tile. Exactly one state in, exactly one
// visual treatment out (Req 19.3 + the Req 23.2 accent mapping). LOCKED is the
// dimmed ink/40 treatment; the active marker's pink is applied separately as an
// overlay, not as a tile state.
function tileAccentClass(state: NodeState): string {
  switch (state) {
    case "MASTERED":
    case "CLEARED":
      return "bg-accent-lime text-ink border-ink";
    case "NEXT":
      return "bg-accent-cyan text-ink border-ink";
    case "DECAYING":
      return "bg-accent-orange text-ink border-ink";
    case "LOCKED":
    default:
      return "bg-white/40 text-ink/40 border-ink/30";
  }
}

// Short, learner-facing label for each state (the quiz is framed as a
// battle/checkpoint, the climb as advancing — Req 23.3).
function stateLabel(state: NodeState): string {
  switch (state) {
    case "MASTERED":
      return "Mastered";
    case "CLEARED":
      return "Cleared";
    case "NEXT":
      return "Next battle";
    case "DECAYING":
      return "Decaying";
    case "LOCKED":
    default:
      return "Locked";
  }
}

// A tiny pixel-art sprite per state, giving each tile a medieval-map read while
// the brutalist card frames it. Goal uses a keep; others use encampments/markers.
function tileSprite(state: NodeState, isGoal: boolean): string {
  if (isGoal) return "🏰";
  switch (state) {
    case "MASTERED":
      return "🏆";
    case "CLEARED":
      return "🚩";
    case "NEXT":
      return "⚔️";
    case "DECAYING":
      return "🔥";
    case "LOCKED":
    default:
      return "🔒";
  }
}

// Resolve a NodeView for a name, synthesizing a LOCKED placeholder when the node
// is referenced (e.g. in `path`) but not yet in the discovered map.
function viewFor(
  name: string,
  nodes: Record<string, NodeView>,
  goal: string,
): NodeView {
  const existing = nodes[name];
  if (existing) return existing;
  return { node: name, state: "LOCKED", is_goal: name === goal };
}

// Build the top→bottom display order: Goal_Node first (boss keep at the top),
// then the descended chain / climb in path order, then any remaining discovered
// nodes. De-duplicates while preserving first-seen order.
function orderedNodeNames(
  nodes: Record<string, NodeView>,
  goal: string,
  path?: string[],
): string[] {
  const order: string[] = [];
  const seen = new Set<string>();
  const push = (name: string) => {
    if (!name || seen.has(name)) return;
    seen.add(name);
    order.push(name);
  };

  push(goal); // boss keep is always at the top
  (path ?? []).forEach(push);
  Object.keys(nodes).forEach(push);
  return order;
}

export default function SkillTreeMap({
  nodes,
  goal,
  currentNode,
  floorNode,
  animateDescentTo,
  onDescentComplete,
  path,
  onSelectNode,
}: SkillTreeMapProps) {
  const names = orderedNodeNames(nodes, goal, path);
  // Stable identity for the ordered tiles so the walk effect only restarts when
  // the actual node ordering changes (not on every render's fresh array).
  const namesKey = names.join("\u0000");

  // Index of the descent target (the Floor_Node) within the top→bottom ordering,
  // or -1 when no animated descent is requested / the target isn't on the map.
  const targetIndex =
    animateDescentTo != null ? names.indexOf(animateDescentTo) : -1;

  // While a descent walk is in flight, `markerIndex` is the tile the adventurer
  // currently stands on; it is null whenever we are not animating (the marker
  // then sits statically on `currentNode`). `descentDone` gates the "You start
  // here" indication so it only appears once the walk lands (Req 18.2).
  const [markerIndex, setMarkerIndex] = useState<number | null>(null);
  const [descentDone, setDescentDone] = useState(false);

  useEffect(() => {
    // No animated descent requested (or target not on the map): clear internal
    // animation state so static placement governs rendering (backward compatible).
    if (animateDescentTo == null || targetIndex < 0) {
      setMarkerIndex(null);
      setDescentDone(false);
      return;
    }

    // Reduced motion, or the floor is already the top tile: land immediately and
    // reveal "You start here" without the step-by-step walk (Req 18 + a11y).
    if (prefersReducedMotion() || targetIndex === 0) {
      setMarkerIndex(targetIndex);
      setDescentDone(true);
      onDescentComplete?.();
      return;
    }

    // Walk DOWN from the boss keep (index 0) to the Floor_Node, one tile per tick
    // (Req 18.1). On arrival, reveal the floor indication (Req 18.2).
    setDescentDone(false);
    setMarkerIndex(0);
    let step = 0;
    const timer = window.setInterval(() => {
      step += 1;
      if (step >= targetIndex) {
        setMarkerIndex(targetIndex);
        setDescentDone(true);
        window.clearInterval(timer);
        onDescentComplete?.();
      } else {
        setMarkerIndex(step);
      }
    }, DESCENT_STEP_MS);

    return () => window.clearInterval(timer);
    // `onDescentComplete` is intentionally excluded to avoid restarting the walk
    // when the parent passes a fresh callback identity each render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [animateDescentTo, targetIndex, namesKey]);

  const animating = markerIndex != null;
  // The tile the marker renders on: the walking position during a descent,
  // otherwise the static current node.
  const markerNode =
    animating && markerIndex != null && markerIndex < names.length
      ? names[markerIndex]
      : currentNode;

  // During an animated descent the floor indication is withheld until the walk
  // finishes (Req 18.2); outside an animated descent the `floorNode` prop drives
  // it exactly as before.
  const effectiveFloor =
    animateDescentTo != null && targetIndex >= 0
      ? descentDone
        ? animateDescentTo
        : null
      : floorNode;

  return (
    <div
      className="relative w-full overflow-hidden brutal bg-paper p-4 sm:p-6"
      aria-label="Skill tree map"
      role="img"
    >
      {/* Pixel-art medieval map background/sprite layer. A blocky grass field
          (stepped repeating gradients evoke pixel tiles) under the brutalist
          chrome. Pointer-events disabled so the tiles above stay interactive. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          imageRendering: "pixelated",
          backgroundColor: "#A8E80C",
          backgroundImage:
            // two offset checker layers = chunky pixel-grass tiles
            "repeating-linear-gradient(0deg, rgba(17,17,17,0.06) 0 8px, transparent 8px 16px)," +
            "repeating-linear-gradient(90deg, rgba(17,17,17,0.06) 0 8px, transparent 8px 16px)," +
            "linear-gradient(180deg, #C8F26B 0%, #A8E80C 45%, #7FB806 100%)",
          opacity: 0.35,
        }}
      />

      {/* The world title — framed as a quest map. */}
      <div className="relative mb-4 flex items-center justify-between gap-2">
        <span className="brutal bg-accent-yellow text-ink text-[11px] font-extrabold uppercase tracking-widest px-2 py-1">
          Quest Map
        </span>
        <span className="text-ink/60 text-[10px] font-bold uppercase tracking-widest">
          Climb to the keep
        </span>
      </div>

      {/* The path of tiles. Goal (boss keep) at the top; the chain descends to
          the floor at the bottom. A blocky vertical trail connects them. */}
      <ol className="relative flex flex-col items-center gap-0">
        {names.map((name, i) => {
          const view = viewFor(name, nodes, goal);
          const isGoal = view.is_goal || name === goal;
          const isCurrent = name === markerNode;
          const isFloor = effectiveFloor != null && name === effectiveFloor;
          const interactive = typeof onSelectNode === "function";

          return (
            <li
              key={name}
              className="relative flex w-full flex-col items-center"
            >
              {/* Trail segment above each tile (skip above the boss keep). */}
              {i > 0 && (
                <span
                  aria-hidden
                  className="h-6 w-[6px] bg-ink"
                  style={{ imageRendering: "pixelated" }}
                />
              )}

              {/* The adventurer marker (pink) sits on the marker tile — the
                  walking position during a descent (Req 18.1), otherwise the
                  static current node. This is the Req 23.2 pink marker. */}
              {isCurrent && (
                <div
                  className="brutal bg-accent-pink text-white text-[10px] font-extrabold uppercase tracking-wider px-2 py-0.5 mb-1 flex items-center gap-1"
                  aria-label={animating && !descentDone ? "Walking down" : "You are here"}
                >
                  <span aria-hidden>🧙</span>{" "}
                  {animating && !descentDone ? "Walking down…" : "You are here"}
                </div>
              )}

              <TileButton
                view={view}
                isGoal={isGoal}
                isCurrent={isCurrent}
                interactive={interactive}
                onSelect={onSelectNode}
              />

              {/* "You start here" indication at the Floor_Node (Req 6.4). */}
              {isFloor && (
                <div className="brutal bg-accent-pink text-white text-[10px] font-extrabold uppercase tracking-wider px-2 py-1 mt-1">
                  You start here
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

// A single map tile. Brutalist chrome (thick ink border, flat accent fill, hard
// shadow) over the pixel-art world. The boss keep (Goal_Node) is rendered larger
// and crowned to read as the top-of-map boss (Req 19.2).
function TileButton({
  view,
  isGoal,
  isCurrent,
  interactive,
  onSelect,
}: {
  view: NodeView;
  isGoal: boolean;
  isCurrent: boolean;
  interactive: boolean;
  onSelect?: (node: string) => void;
}) {
  const accent = tileAccentClass(view.state);
  // The marker's pink ring highlights the current tile without changing its
  // underlying state fill (state stays the single source of truth, Req 19.3).
  const currentRing = isCurrent ? "ring-4 ring-accent-pink ring-offset-2 ring-offset-paper" : "";
  const sizing = isGoal ? "min-w-[200px] px-5 py-4" : "min-w-[160px] px-4 py-3";
  const sprite = tileSprite(view.state, isGoal);

  const inner = (
    <>
      {isGoal && (
        <span className="absolute -top-3 left-1/2 -translate-x-1/2 brutal bg-accent-yellow text-ink text-[9px] font-extrabold uppercase tracking-widest px-2 py-0.5">
          Boss
        </span>
      )}
      <div className="flex items-center gap-2">
        <span
          aria-hidden
          className={isGoal ? "text-2xl" : "text-xl"}
          style={{ imageRendering: "pixelated" }}
        >
          {sprite}
        </span>
        <div className="flex flex-col text-left">
          <span
            className={`font-extrabold leading-tight ${isGoal ? "text-sm" : "text-xs"}`}
          >
            {view.node}
          </span>
          <span className="text-[10px] font-bold uppercase tracking-wider opacity-80">
            {isGoal ? "Goal · " : ""}
            {stateLabel(view.state)}
          </span>
        </div>
      </div>
    </>
  );

  const className = `brutal-card relative flex flex-col items-center ${sizing} ${accent} ${currentRing}`;

  if (interactive) {
    return (
      <button
        type="button"
        onClick={() => onSelect?.(view.node)}
        className={`${className} brutal-btn`}
        aria-current={isCurrent ? "true" : undefined}
      >
        {inner}
      </button>
    );
  }

  return (
    <div className={className} aria-current={isCurrent ? "true" : undefined}>
      {inner}
    </div>
  );
}
