"use client";

// WorldMap — the retro pixel-art reskin of `SkillTreeMap` (Req 3, 11.1, 11.2).
//
// Where `SkillTreeMap` drew a brutalist quest map, `WorldMap` draws a
// Super Mario World / Candy Land–style OVERWORLD: a winding pixel path of
// Stages snaking from the Knight's starting camp up to a Dragon's Keep castle
// at the goal. It is a pure reskin — it preserves the EXACT props and data
// contract of `SkillTreeMap` (the `NodeView`/`NodeState` shapes, the
// `animateDescentTo`/`onDescentComplete`/`DESCENT_STEP_MS` descent-walk
// machinery, and the top→bottom `path` ordering) so the Game_State_Machine
// wiring in `play/page.tsx` only swaps the import (Req 11.2).
//
// Stage_State → sprite/tint is total and 1:1 with `NodeState` (Property 1,
// Req 3.3): MASTERED = gold trophy, CLEARED = lime flag/camp, NEXT = cyan
// monster lair, DECAYING = orange ember, LOCKED = dim fogged tile; the
// Goal_Node renders as the red Dragon's Keep castle (Req 3.2). State is derived
// purely from props, so a Stage re-renders its treatment whenever the node's
// state changes (Req 3.4). The descended prerequisite chain and the climb back
// toward the Dragon are drawn as one continuous winding trail (Req 3.5), and the
// Floor_Node carries a "You start here" camp banner (Req 3.6).
//
// The Knight sprite (`./Knight`) is mounted on the Knight's current tile. This
// component reuses the SkillTreeMap descent-walk machinery to compute WHICH tile
// the Knight stands on (`markerNode`) and exposes a placement anchor on that
// tile (`data-knight-anchor`, with a `data-walking` flag) into which the
// `<Knight />` sprite is dropped, so the Knight hops node-to-node on moves.
//
// All retro colors come from the `--pq-*` tokens scoped under `.pixel-quest`
// (see `frontend/app/retro.css`); tiles use the `.pixelated` class so the art
// stays crisp at any scale (Req 1.2).

import { useEffect, useRef, useState } from "react";

import { type NodeState, type NodeView } from "@/lib/game-progress";

import { type Biome, biomeBackdrop, biomeLabel, worldFor } from "./biomes";
import Knight from "./Knight";
import { useRetroSettings } from "./RetroThemeProvider";

// Milliseconds the Knight rests on each tile while walking the trail. Kept in
// lockstep with `retro.css`'s `--pq-walk-step-ms` token and mirroring
// `SkillTreeMap.DESCENT_STEP_MS` so the descent-walk contract is unchanged.
// Exported so an interaction test (and task 2.2's Knight) can drive the
// animation deterministically.
export const DESCENT_STEP_MS = 450;

// How long the brief cross-World transition beat stays on screen when the
// Knight crosses from one themed World into the next (Req 15.2). Kept short so
// it reads as a beat, not an interruption; the overlay never blocks input.
export const BIOME_TRANSITION_MS = 900;

// True when the user has asked the OS to reduce motion. SSR-guarded and wrapped
// so an environment without `matchMedia` never throws (the walk simply runs).
// Mirrors `SkillTreeMap.prefersReducedMotion()` exactly (Req 1.4, 12.1).
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

// Props are intentionally identical to `SkillTreeMapProps` save for the
// `onSelectNode` → `onSelectStage` rename (per the design's `WorldMapProps`),
// so the reskin is a drop-in swap (Req 11.2).
export interface WorldMapProps {
  // Discovered nodes for the map, keyed by node name (mirrors
  // GameSessionState.nodes). Each renders as a Stage in its single Stage_State.
  nodes: Record<string, NodeView>;
  // The Goal_Node (entered topic) — rendered as the Dragon's Keep castle.
  goal: string;
  // The node the Knight currently stands on.
  currentNode: string;
  // The Floor_Node ("You start here" camp), or null before a floor is set.
  floorNode: string | null;
  // Ordered visited nodes (descent chain + climb). Orders Stages top→bottom
  // (Dragon's Keep at the top, starting camp at the bottom). Optional: when
  // omitted the map falls back to the discovered-node keys with the goal pinned
  // to the top.
  path?: string[];
  // Descent animation (Req 3.5/3.6 + 2.x). When set, the Knight walks DOWN the
  // trail node-by-node from the keep until it lands on this node (the
  // established Floor_Node), and the "You start here" camp is withheld until the
  // walk completes. When omitted/null the Knight is placed statically at
  // `currentNode` and the camp follows `floorNode` (backward compatible).
  animateDescentTo?: string | null;
  // Fired once the descent walk reaches the Floor_Node (also fired immediately
  // under reduced motion). Useful for sequencing the Battle screen and tests.
  onDescentComplete?: () => void;
  // Optional click handler for a Stage (e.g. to inspect a node or, in Phase 2,
  // pick a fork). Non-interactive when omitted.
  onSelectStage?: (node: string) => void;
}

// Per-state retro tint, drawn from the `--pq-*` palette. Exactly one state in,
// exactly one visual treatment out (Property 1, Req 3.3). LOCKED is the dim
// "fogged" treatment. The goal's Dragon's Keep red is applied separately.
function stageTint(state: NodeState): {
  fill: string;
  border: string;
  text: string;
  glow?: string;
} {
  switch (state) {
    case "MASTERED":
      // Slain-monster trophy — gold.
      return { fill: "var(--pq-gold)", border: "var(--pq-ink)", text: "var(--pq-ink)" };
    case "CLEARED":
      // Cleared camp / planted flag — lime/green.
      return { fill: "var(--pq-lime)", border: "var(--pq-ink)", text: "var(--pq-ink)" };
    case "NEXT":
      // Live monster lair — cyan glow.
      return {
        fill: "var(--pq-cyan)",
        border: "var(--pq-ink)",
        text: "var(--pq-ink)",
        glow: "var(--pq-cyan)",
      };
    case "DECAYING":
      // Re-spawning monster — orange ember.
      return { fill: "var(--pq-orange)", border: "var(--pq-ink)", text: "var(--pq-ink)" };
    case "LOCKED":
    default:
      // Fogged / undiscovered — dim.
      return { fill: "var(--pq-locked)", border: "var(--pq-ink)", text: "var(--pq-text)" };
  }
}

// Short, learner-facing label for each Stage_State, framed as an adventure beat.
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

// A pixel-art sprite per Stage_State. The Goal_Node is the Dragon's Keep; the
// others are the overworld lair/camp/trophy/ember/fog markers.
function stageSprite(state: NodeState, isGoal: boolean): string {
  if (isGoal) return "🐉";
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
      return "🌫️";
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

// Build the top→bottom display order: Goal_Node first (Dragon's Keep at the top
// of the climb), then the descended chain / climb in path order, then any
// remaining discovered nodes. De-duplicates while preserving first-seen order.
// Identical to `SkillTreeMap.orderedNodeNames` so the trail order is unchanged.
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

  push(goal); // Dragon's Keep is always at the top of the trail
  (path ?? []).forEach(push);
  Object.keys(nodes).forEach(push);
  return order;
}

// Horizontal offset (in tile-steps) for a Stage at a given trail index, giving
// the path its winding board-game snake. A smooth zig-zag cycle keeps the trail
// readable while evoking a Mario/Candyland overworld. Pure function of index so
// the layout is deterministic and test-stable.
const WIND_PATTERN = [0, 1, 2, 1, 0, -1, -2, -1];
function windOffset(index: number): number {
  return WIND_PATTERN[index % WIND_PATTERN.length];
}

// Pixels per wind step — the amplitude of the snake.
const WIND_STEP_PX = 56;

export default function WorldMap({
  nodes,
  goal,
  currentNode,
  floorNode,
  path,
  animateDescentTo,
  onDescentComplete,
  onSelectStage,
}: WorldMapProps) {
  const names = orderedNodeNames(nodes, goal, path);
  // Stable identity for the ordered tiles so the walk effect only restarts when
  // the actual node ordering changes (not on every render's fresh array).
  const namesKey = names.join("\u0000");

  // Index of the descent target (the Floor_Node) within the top→bottom
  // ordering, or -1 when no animated descent is requested / not on the map.
  const targetIndex =
    animateDescentTo != null ? names.indexOf(animateDescentTo) : -1;

  // While a descent walk is in flight, `markerIndex` is the tile the Knight
  // currently stands on; null whenever we are not animating (the Knight then
  // sits statically on `currentNode`). `descentDone` gates the "You start here"
  // camp so it only appears once the walk lands (Req 3.6). This is the exact
  // descent-walk machinery from `SkillTreeMap`, preserved for task 2.2.
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
    // reveal the camp without the step-by-step walk (Req 1.4, 12.1).
    if (prefersReducedMotion() || targetIndex === 0) {
      setMarkerIndex(targetIndex);
      setDescentDone(true);
      onDescentComplete?.();
      return;
    }

    // Walk DOWN from the Dragon's Keep (index 0) to the Floor_Node, one tile per
    // tick (Req 3.5). On arrival, reveal the starting-camp banner (Req 3.6).
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
  // The tile the Knight renders on: the walking position during a descent,
  // otherwise the static current node.
  const markerNode =
    animating && markerIndex != null && markerIndex < names.length
      ? names[markerIndex]
      : currentNode;

  // Reduced motion comes from the RetroThemeProvider (Req 1.4, 12.1); fall back
  // to the SSR-guarded media read so the rule holds even when WorldMap is
  // rendered outside a provider (e.g. isolated component tests).
  const { reducedMotion } = useRetroSettings();
  const motionReduced = reducedMotion || prefersReducedMotion();

  // ── Cross-World biome transition beat (Req 15.2) ─────────────────────────
  // Each themed World owns a band of the trail (see `worldFor`). The Knight's
  // World is the biome of whatever tile it currently stands on. When that World
  // changes — i.e. the Knight crosses a biome boundary as it walks or advances —
  // we flash a brief "Now entering {World}" beat. The beat is purely cosmetic,
  // auto-dismisses after `BIOME_TRANSITION_MS`, and is suppressed entirely under
  // reduced motion (the static end-state is simply the new World's backdrop).
  const markerNameIndex = names.indexOf(markerNode);
  const knightBiome: Biome | null =
    markerNameIndex >= 0
      ? worldFor(names.length - 1 - markerNameIndex, names.length)
      : null;

  const [transitionBiome, setTransitionBiome] = useState<Biome | null>(null);
  // Remembers the Knight's World across renders so we only fire on an actual
  // crossing (not the first paint, and not on unrelated re-renders).
  const prevBiomeRef = useRef<Biome | null>(null);

  useEffect(() => {
    const prev = prevBiomeRef.current;
    prevBiomeRef.current = knightBiome;

    // No crossing if we don't yet know the World, it hasn't changed, or this is
    // the first time we've seen one (initial placement is not a "crossing").
    if (knightBiome == null || prev == null || knightBiome === prev) {
      return;
    }
    // Honor reduced motion: snap to the new World with no transition beat.
    if (motionReduced) {
      setTransitionBiome(null);
      return;
    }

    setTransitionBiome(knightBiome);
    const timer = window.setTimeout(
      () => setTransitionBiome(null),
      BIOME_TRANSITION_MS,
    );
    return () => window.clearTimeout(timer);
  }, [knightBiome, motionReduced]);

  // During an animated descent the camp is withheld until the walk finishes
  // (Req 3.6); outside an animated descent the `floorNode` prop drives it.
  const effectiveFloor =
    animateDescentTo != null && targetIndex >= 0
      ? descentDone
        ? animateDescentTo
        : null
      : floorNode;

  return (
    <div
      className="pixelated relative w-full overflow-hidden"
      aria-label="World map"
      role="img"
      style={{
        background:
          "linear-gradient(180deg, #7ec7f0 0%, #aee0f2 36%, #dff0c4 50%, #79c46f 66%, #4f9f5a 100%)",
        border: "4px solid var(--pq-ink)",
        boxShadow: "0 0 0 4px var(--pq-panel)",
        padding: "1.25rem 1rem 1.5rem",
        minHeight: "300px",
      }}
    >
      {/* Layered pixel scenery (research: depth via layering + a limited
          palette): a sun, drifting clouds, a dithered horizon, and chunky grass
          — replaces the flat gradient with a real overworld backdrop. All
          aria-hidden + pointer-events-none so the trail above stays interactive. */}
      <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
        {/* sun */}
        <div
          className="pixelated"
          style={{ position: "absolute", top: 14, right: 22, width: 44, height: 44, borderRadius: "50%", background: "#ffe169", border: "3px solid var(--pq-ink)", boxShadow: "0 0 0 6px rgba(255,225,105,0.30)" }}
        />
        {/* clouds */}
        <div className="pixelated" style={{ position: "absolute", top: 28, left: "16%", width: 66, height: 16, background: "#ffffff", border: "3px solid var(--pq-ink)", opacity: 0.9 }} />
        <div className="pixelated" style={{ position: "absolute", top: 58, left: "52%", width: 50, height: 14, background: "#ffffff", border: "3px solid var(--pq-ink)", opacity: 0.75 }} />
        {/* dithered horizon band where sky meets land */}
        <div
          className="pixelated"
          style={{ position: "absolute", left: 0, right: 0, top: "48%", height: 18, backgroundImage: "repeating-linear-gradient(90deg, rgba(26,20,38,0.18) 0 6px, transparent 6px 12px)", opacity: 0.6 }}
        />
        {/* chunky grass dither along the ground */}
        <div
          className="pixelated"
          style={{ position: "absolute", left: 0, right: 0, bottom: 0, height: "34%", backgroundImage: "repeating-linear-gradient(0deg, rgba(26,20,38,0.12) 0 6px, transparent 6px 12px), repeating-linear-gradient(90deg, rgba(26,20,38,0.10) 0 6px, transparent 6px 12px)", opacity: 0.5 }}
        />
      </div>

      {/* Overworld title bar. */}
      <div className="relative mb-5 flex items-center justify-between gap-2">
        <span
          className="pixel-font"
          style={{
            background: "var(--pq-gold)",
            color: "var(--pq-ink)",
            border: "3px solid var(--pq-ink)",
            padding: "0.25rem 0.5rem",
            fontSize: "0.6rem",
            textTransform: "uppercase",
            letterSpacing: "0.1em",
          }}
        >
          World Map
        </span>
        <span
          className="pixel-font"
          style={{
            color: "var(--pq-text)",
            fontSize: "0.55rem",
            textTransform: "uppercase",
            letterSpacing: "0.1em",
            opacity: 0.85,
          }}
        >
          March to the Keep
        </span>
      </div>

      {/* The winding trail of Stages. Goal (Dragon's Keep) at the top; the chain
          winds down to the starting camp at the bottom. Each Stage is offset
          horizontally by `windOffset` to snake like a board-game overworld, and
          a pixel path segment connects each Stage to the previous one. */}
      <ol className="relative flex flex-col items-center gap-0">
        {names.map((name, i) => {
          const view = viewFor(name, nodes, goal);
          const isGoal = view.is_goal || name === goal;
          const isCurrent = name === markerNode;
          const isFloor = effectiveFloor != null && name === effectiveFloor;
          const interactive = typeof onSelectStage === "function";

          const offset = windOffset(i) * WIND_STEP_PX;
          const prevOffset = i > 0 ? windOffset(i - 1) * WIND_STEP_PX : offset;

          // Themed-World tint for this Stage by its position on the JOURNEY.
          // `names` runs goal-first (Dragon's Keep at index 0) down to the
          // starting camp, so the journey position is the reverse: the bottom
          // tile is Candyland (0) and the top tile is the Dragon's Keep
          // (total - 1). Phase 1 uses this only for a subtle background tint
          // (Req 3.5); it carries no engine state.
          const biome = worldFor(names.length - 1 - i, names.length);

          return (
            <li
              key={name}
              data-biome={biome}
              className="relative flex w-full flex-col items-center"
            >
              {/* Distinct themed-World backdrop for this Stage's band of the
                  trail (Req 15.1). Each World layers its own dither/pattern over
                  its tint via `biomeBackdrop` so the Worlds read as distinct
                  places; kept low-opacity so the Stage tiles and winding path
                  above stay readable. Pointer events disabled. */}
              <div
                aria-hidden
                data-biome-backdrop={biome}
                className="pixelated pointer-events-none absolute inset-0"
                style={{ background: biomeBackdrop(biome), opacity: 0.24 }}
              />
              {/* Winding path segment above each Stage (skip above the keep).
                  Slanted toward the previous tile's offset to read as a trail. */}
              {i > 0 && (
                <span
                  aria-hidden
                  className="pixelated"
                  style={{
                    display: "block",
                    height: "1.75rem",
                    width: "8px",
                    background: "var(--pq-paper)",
                    border: "2px solid var(--pq-ink)",
                    // lean the segment toward the midpoint of the two offsets
                    transform: `translateX(${(offset + prevOffset) / 2}px) rotate(${
                      offset === prevOffset ? 0 : offset > prevOffset ? 12 : -12
                    }deg)`,
                  }}
                />
              )}

              <div
                className="relative flex flex-col items-center"
                style={{ transform: `translateX(${offset}px)` }}
              >
                {/* The Knight sprite sits on the Knight's current tile — the
                    walking position during a descent (Req 2.2/2.3), otherwise
                    the static current node (Req 2.1). The anchor wrapper carries
                    the `data-knight-anchor`/`data-walking` hooks (used by the
                    interaction test) while the `<Knight />` sprite renders the
                    idle/walk frames; reduced motion is handled in retro.css. */}
                {isCurrent && (
                  <div
                    data-knight-anchor="true"
                    data-walking={animating && !descentDone ? "true" : "false"}
                    style={{ marginBottom: "0.35rem", lineHeight: 1 }}
                  >
                    <Knight walking={animating && !descentDone} size={56} />
                  </div>
                )}

                <StageTile
                  view={view}
                  isGoal={isGoal}
                  isCurrent={isCurrent}
                  interactive={interactive}
                  onSelect={onSelectStage}
                />

                {/* "You start here" camp at the Floor_Node (Req 3.6). */}
                {isFloor && (
                  <div
                    className="pixel-font"
                    style={{
                      marginTop: "0.35rem",
                      background: "var(--pq-lime)",
                      color: "var(--pq-ink)",
                      border: "3px solid var(--pq-ink)",
                      padding: "0.2rem 0.45rem",
                      fontSize: "0.55rem",
                      textTransform: "uppercase",
                      letterSpacing: "0.06em",
                    }}
                  >
                    ⛺ You start here
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>

      {/* Brief cross-World transition beat (Req 15.2). Mounts only when the
          Knight has just crossed into a new World and motion is allowed; it
          auto-dismisses after BIOME_TRANSITION_MS. `pq-biome-transition` runs a
          short stepwise wipe (retro.css), which reduced motion hard-disables —
          and we additionally never mount this under reduced motion. The overlay
          is non-interactive so it never blocks the trail beneath it. */}
      {transitionBiome && (
        <div
          role="status"
          aria-live="polite"
          data-biome-transition={transitionBiome}
          className="pixelated pq-biome-transition pointer-events-none absolute inset-0 flex items-center justify-center"
          style={{ zIndex: 40, background: biomeBackdrop(transitionBiome) }}
        >
          <span
            className="pixel-font"
            style={{
              background: "var(--pq-ink)",
              color: "var(--pq-text)",
              border: "4px solid var(--pq-text)",
              padding: "0.5rem 0.9rem",
              fontSize: "0.7rem",
              textTransform: "uppercase",
              letterSpacing: "0.1em",
              boxShadow: "4px 4px 0 0 var(--pq-ink)",
            }}
          >
            Now entering {biomeLabel(transitionBiome)}
          </span>
        </div>
      )}
    </div>
  );
}

// A single overworld Stage. Pixel-art tile (chunky ink border, flat tint, hard
// shadow) over the winding trail. The Dragon's Keep (Goal_Node) renders larger
// with a castle frame and crown so it reads as the destination boss (Req 3.2).
function StageTile({
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
  const tint = isGoal
    ? { fill: "var(--pq-red)", border: "var(--pq-ink)", text: "var(--pq-text)", glow: "var(--pq-red)" }
    : stageTint(view.state);
  const sprite = stageSprite(view.state, isGoal);

  const boxShadow = [
    "4px 4px 0 0 var(--pq-ink)",
    isCurrent ? "0 0 0 4px var(--pq-pink)" : "",
    tint.glow ? `0 0 14px 2px ${tint.glow}` : "",
  ]
    .filter(Boolean)
    .join(", ");

  const className = "pixelated relative flex flex-col items-center";
  const style: React.CSSProperties = {
    minWidth: isGoal ? 220 : 168,
    padding: isGoal ? "1rem 1.25rem" : "0.6rem 0.9rem",
    background: tint.fill,
    color: tint.text,
    border: "4px solid",
    borderColor: tint.border,
    boxShadow,
  };

  const inner = (
    <>
      {isGoal && (
        <span
          className="pixel-font"
          style={{
            position: "absolute",
            top: "-0.85rem",
            left: "50%",
            transform: "translateX(-50%)",
            background: "var(--pq-gold)",
            color: "var(--pq-ink)",
            border: "3px solid var(--pq-ink)",
            padding: "0.1rem 0.4rem",
            fontSize: "0.5rem",
            textTransform: "uppercase",
            letterSpacing: "0.1em",
            whiteSpace: "nowrap",
          }}
        >
          🏰 Dragon's Keep
        </span>
      )}
      <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
        <span
          aria-hidden
          className="pixelated"
          style={{ fontSize: isGoal ? "1.75rem" : "1.35rem", lineHeight: 1 }}
        >
          {sprite}
        </span>
        <div style={{ display: "flex", flexDirection: "column", textAlign: "left" }}>
          <span
            className="pixel-font"
            style={{
              fontSize: isGoal ? "0.65rem" : "0.55rem",
              lineHeight: 1.4,
              fontWeight: 700,
            }}
          >
            {view.node}
          </span>
          <span
            className="pixel-font"
            style={{
              fontSize: "0.45rem",
              textTransform: "uppercase",
              letterSpacing: "0.08em",
              opacity: 0.85,
            }}
          >
            {isGoal ? "Goal · Dragon" : stateLabel(view.state)}
          </span>
        </div>
      </div>
    </>
  );

  if (interactive) {
    return (
      <button
        type="button"
        onClick={() => onSelect?.(view.node)}
        className={className}
        style={style}
        aria-current={isCurrent ? "true" : undefined}
      >
        {inner}
      </button>
    );
  }

  return (
    <div
      className={className}
      style={style}
      aria-current={isCurrent ? "true" : undefined}
    >
      {inner}
    </div>
  );
}
