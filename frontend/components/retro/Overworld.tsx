"use client";

// Overworld — the playable top-down map (Pokémon-style). Thin React renderer over
// the pure engine in lib/overworld.ts: it owns only view concerns (DOM, input,
// the per-step tween) while all movement/collision/interaction rules live in the
// tested core. Grid-locked movement: each input steps the player to an adjacent
// tile; the world slides under a fixed camera that keeps the player centered.
//
// Controls: Arrow keys / WASD to walk, Space/Enter to interact with a faced node.
// On-screen D-pad + (A) button mirror this for touch. Walk bob + slide are
// disabled under reduced motion (the .pq-walk cycle is already neutralised in
// retro.css; the camera transition is dropped here too).

import { useEffect, useRef, useState, type CSSProperties } from "react";
import {
  cameraOffset,
  interact,
  tryMove,
  type Dir,
  type MapNode,
  type OverworldMap,
  type PlayerState,
} from "@/lib/overworld";

const TILE = 40; // px per tile
const VIEW_X = 11; // viewport width in tiles (odd → player centered)
const VIEW_Y = 9; // viewport height in tiles
const STEP_MS = 160; // per-tile step duration

const KEY_DIR: Record<string, Dir> = {
  ArrowUp: "up",
  ArrowDown: "down",
  ArrowLeft: "left",
  ArrowRight: "right",
  w: "up",
  s: "down",
  a: "left",
  d: "right",
  W: "up",
  S: "down",
  A: "left",
  D: "right",
};

export interface OverworldProps {
  map: OverworldMap;
  onEnterNode: (node: MapNode) => void;
}

export default function Overworld({ map, onEnterNode }: OverworldProps) {
  const [player, setPlayer] = useState<PlayerState>({ pos: map.start, facing: "down" });
  const [walking, setWalking] = useState(false);
  const [reduced, setReduced] = useState(false);

  // Refs hold the live values the input/step loop reads, dodging stale closures.
  const playerRef = useRef<PlayerState>(player);
  const movingRef = useRef(false);
  const heldRef = useRef<Dir[]>([]);
  const reducedRef = useRef(false);
  const stepRef = useRef<() => void>(() => {});
  playerRef.current = player;
  reducedRef.current = reduced;

  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => setReduced(mq.matches);
    apply();
    mq.addEventListener?.("change", apply);
    return () => mq.removeEventListener?.("change", apply);
  }, []);

  // Reassigned every render so it always closes over the current `map`.
  stepRef.current = () => {
    if (movingRef.current) return;
    const dir = heldRef.current[heldRef.current.length - 1];
    if (!dir) return;
    const res = tryMove(map, playerRef.current, dir);
    playerRef.current = res.player;
    setPlayer(res.player);
    if (res.moved) {
      movingRef.current = true;
      setWalking(true);
      const ms = reducedRef.current ? 0 : STEP_MS;
      window.setTimeout(() => {
        movingRef.current = false;
        setWalking(false);
        stepRef.current(); // continue walking if a key is still held
      }, ms);
    }
    // A bump (no move) just turned the player; wait for the next input.
  };

  function pushDir(dir: Dir) {
    heldRef.current = heldRef.current.filter((d) => d !== dir);
    heldRef.current.push(dir);
  }
  function removeDir(dir: Dir) {
    heldRef.current = heldRef.current.filter((d) => d !== dir);
  }
  function doInteract() {
    if (movingRef.current) return;
    const n = interact(map, playerRef.current);
    if (n) onEnterNode(n);
  }

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      const dir = KEY_DIR[e.key];
      if (dir) {
        e.preventDefault();
        pushDir(dir);
        if (!movingRef.current) stepRef.current();
        return;
      }
      if (e.key === " " || e.key === "Enter") {
        e.preventDefault();
        doInteract();
      }
    }
    function onKeyUp(e: KeyboardEvent) {
      const dir = KEY_DIR[e.key];
      if (dir) removeDir(dir);
    }
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, onEnterNode]);

  const view = { w: VIEW_X * TILE, h: VIEW_Y * TILE };
  const cam = cameraOffset(player, view, map, TILE);

  return (
    <div className="pixel-quest" style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 14 }}>
      {/* Viewport (camera) */}
      <div
        style={{
          position: "relative",
          width: view.w,
          height: view.h,
          maxWidth: "100%",
          overflow: "hidden",
          border: "4px solid var(--pq-ink)",
          boxShadow: "6px 6px 0 0 var(--pq-ink)",
          background: "var(--pq-bg)",
          imageRendering: "pixelated",
        }}
      >
        {/* World (slides under the camera) */}
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            width: map.width * TILE,
            height: map.height * TILE,
            transform: `translate3d(${cam.x}px, ${cam.y}px, 0)`,
            transition: reduced ? "none" : `transform ${STEP_MS}ms linear`,
            willChange: "transform",
          }}
        >
          {map.tiles.map((row, y) =>
            row.map((kind, x) => (
              <div
                key={`${x},${y}`}
                style={{
                  position: "absolute",
                  left: x * TILE,
                  top: y * TILE,
                  width: TILE,
                  height: TILE,
                  background: tileBg(kind, x, y),
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: Math.round(TILE * 0.66),
                  lineHeight: 1,
                }}
              >
                {tileGlyph(kind)}
              </div>
            )),
          )}

          {/* Topic nodes */}
          {Object.values(map.nodes).map((n) => (
            <div
              key={n.id}
              style={{
                position: "absolute",
                left: n.x * TILE,
                top: n.y * TILE,
                width: TILE,
                height: TILE,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <div
                style={{
                  width: Math.round(TILE * 0.82),
                  height: Math.round(TILE * 0.82),
                  border: "3px solid var(--pq-ink)",
                  background: nodeColor(n.state),
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: Math.round(TILE * 0.46),
                  opacity: n.state === "locked" ? 0.65 : 1,
                }}
              >
                {nodeGlyph(n.state)}
              </div>
              <span
                style={{
                  position: "absolute",
                  top: TILE * 0.9,
                  fontFamily: "var(--pq-font-pixel)",
                  fontSize: 7,
                  color: "var(--pq-text)",
                  whiteSpace: "nowrap",
                  textShadow: "1px 1px 0 #000",
                }}
              >
                {n.label}
              </span>
            </div>
          ))}

          {/* Player */}
          <div
            style={{
              position: "absolute",
              left: player.pos.x * TILE,
              top: player.pos.y * TILE,
              width: TILE,
              height: TILE,
              display: "flex",
              alignItems: "flex-end",
              justifyContent: "center",
              paddingBottom: 2,
            }}
          >
            <Hero facing={player.facing} walking={walking} />
          </div>
        </div>
      </div>

      {/* Touch controls + hint */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: view.w, maxWidth: "100%" }}>
        <DPad onPress={(d) => { pushDir(d); if (!movingRef.current) stepRef.current(); }} onRelease={removeDir} />
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6 }}>
          <button
            aria-label="Interact"
            onPointerDown={(e) => { e.preventDefault(); doInteract(); }}
            style={{ ...padBtnStyle, width: 56, height: 56, borderRadius: "50%", background: "var(--pq-red)", color: "var(--pq-text)", fontSize: 18 }}
          >
            A
          </button>
          <span style={{ fontFamily: "var(--pq-font-pixel)", fontSize: 7, color: "var(--pq-text)", opacity: 0.8 }}>
            talk
          </span>
        </div>
      </div>
      <p style={{ fontFamily: "var(--pq-font-pixel)", fontSize: 8, color: "var(--pq-text)", opacity: 0.8, textAlign: "center" }}>
        Arrow keys / WASD to walk · Space to interact
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Presentational helpers
// ---------------------------------------------------------------------------

function tileBg(kind: TileKind, x: number, y: number): string {
  const even = (x + y) % 2 === 0;
  switch (kind) {
    case "path":
      return even ? "#e2cd95" : "#d9c188";
    case "tree":
      return "#2f7d4f";
    case "water":
      return even ? "#3f8fe0" : "#3a84d6";
    case "wall":
      return "#6b6480";
    case "grass":
    case "flower":
    case "node":
    default:
      return even ? "#57bd78" : "#4caf6d";
  }
}

type TileKind = OverworldMap["tiles"][number][number];

function tileGlyph(kind: TileKind): string {
  switch (kind) {
    case "tree":
      return "🌳";
    case "flower":
      return "🌼";
    default:
      return "";
  }
}

function nodeColor(state: MapNode["state"]): string {
  switch (state) {
    case "goal":
      return "var(--pq-red)";
    case "cleared":
      return "var(--pq-lime)";
    case "locked":
      return "var(--pq-locked)";
    case "next":
    default:
      return "var(--pq-cyan)";
  }
}

function nodeGlyph(state: MapNode["state"]): string {
  switch (state) {
    case "goal":
      return "🏰";
    case "cleared":
      return "🏳️";
    case "locked":
      return "🔒";
    case "next":
    default:
      return "❗";
  }
}

// A tiny CSS pixel character. Facing is conveyed by eye placement (no eyes when
// facing "up" — you see the back of the head). Real sprite sheets can replace
// this later, same as the Knight component's substitution note.
function Hero({ facing, walking }: { facing: Dir; walking: boolean }) {
  const skin = "#f2c79b";
  const hair = "#2a1d12";
  const shirt = "var(--pq-cyan)";
  const eye = "#1a1426";

  const eyeStyle = (which: "l" | "r"): CSSProperties => {
    const base: CSSProperties = { position: "absolute", width: "16%", height: "14%", background: eye };
    if (facing === "down") return { ...base, top: "34%", ...(which === "l" ? { left: "22%" } : { right: "22%" }) };
    if (facing === "left") return { ...base, top: "34%", left: which === "l" ? "14%" : "36%" };
    // right
    return { ...base, top: "34%", right: which === "l" ? "14%" : "36%" };
  };

  return (
    <div
      className={`pixelated ${walking ? "pq-walk" : ""}`}
      style={{
        position: "relative",
        width: Math.round(TILE * 0.6),
        height: Math.round(TILE * 0.78),
        filter: "drop-shadow(2px 2px 0 rgba(0,0,0,0.4))",
      }}
    >
      <div style={{ position: "absolute", top: 0, left: 0, right: 0, height: "30%", background: hair }} />
      <div style={{ position: "absolute", top: "18%", left: 0, right: 0, height: "40%", background: skin }} />
      <div style={{ position: "absolute", bottom: 0, left: 0, right: 0, height: "46%", background: shirt }} />
      {facing !== "up" && (
        <>
          <span style={eyeStyle("l")} />
          <span style={eyeStyle("r")} />
        </>
      )}
    </div>
  );
}

const padBtnStyle: CSSProperties = {
  border: "3px solid var(--pq-ink)",
  background: "var(--pq-panel)",
  color: "var(--pq-text)",
  fontFamily: "var(--pq-font-pixel)",
  fontSize: 12,
  width: 44,
  height: 44,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  cursor: "pointer",
  touchAction: "none",
  userSelect: "none",
};

function DPad({ onPress, onRelease }: { onPress: (d: Dir) => void; onRelease: (d: Dir) => void }) {
  const btn = (d: Dir, glyph: string) => (
    <button
      aria-label={d}
      onPointerDown={(e) => { e.preventDefault(); onPress(d); }}
      onPointerUp={() => onRelease(d)}
      onPointerLeave={() => onRelease(d)}
      onPointerCancel={() => onRelease(d)}
      style={padBtnStyle}
    >
      {glyph}
    </button>
  );
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 44px)", gridTemplateRows: "repeat(3, 44px)", gap: 4 }}>
      <span />
      {btn("up", "▲")}
      <span />
      {btn("left", "◀")}
      <span />
      {btn("right", "▶")}
      <span />
      {btn("down", "▼")}
      <span />
    </div>
  );
}
