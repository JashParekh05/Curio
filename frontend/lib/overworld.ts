// Pure, deterministic core for the top-down overworld (Pokémon-style grid
// movement). No React, no DOM, no globals — so it's unit-testable and the React
// layer (components/retro/Overworld.tsx) is a thin renderer over this state.
//
// Movement is GRID-LOCKED like classic Pokémon: the player occupies one tile and
// steps to an adjacent tile if it's walkable; blocked tiles (trees, water, walls,
// and "node" tiles) stop movement. You interact with a node by FACING it and
// pressing the action key — you never stand on it.

export type Dir = "up" | "down" | "left" | "right";

export interface Vec {
  x: number;
  y: number;
}

export type TileKind =
  | "grass"
  | "path"
  | "flower"
  | "tree"
  | "water"
  | "wall"
  | "node";

// A point of interest on the map: a topic "stage" the learner walks up to.
export interface MapNode {
  id: string;
  x: number;
  y: number;
  label: string;
  topic: string; // the query handed to the learning loop (/play?topic=)
  state: "next" | "cleared" | "locked" | "goal";
}

export interface OverworldMap {
  width: number;
  height: number;
  tiles: TileKind[][]; // indexed [y][x]
  nodes: Record<string, MapNode>; // keyed "x,y"
  start: Vec;
}

export interface PlayerState {
  pos: Vec;
  facing: Dir;
}

export interface ViewSize {
  w: number; // viewport width in px
  h: number; // viewport height in px
}

// Unit-step delta per direction (y grows downward, screen coordinates).
export const DIRS: Record<Dir, Vec> = {
  up: { x: 0, y: -1 },
  down: { x: 0, y: 1 },
  left: { x: -1, y: 0 },
  right: { x: 1, y: 0 },
};

// Tile kinds the player cannot enter. Nodes are blocked: you face them to talk.
export const BLOCKED: ReadonlySet<TileKind> = new Set<TileKind>([
  "tree",
  "water",
  "wall",
  "node",
]);

export function addVec(a: Vec, b: Vec): Vec {
  return { x: a.x + b.x, y: a.y + b.y };
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, n));
}

// Is (x,y) in-bounds and steppable?
export function isWalkable(map: OverworldMap, x: number, y: number): boolean {
  if (x < 0 || y < 0 || x >= map.width || y >= map.height) return false;
  return !BLOCKED.has(map.tiles[y][x]);
}

// Attempt a step. ALWAYS turns the player to `dir` (Pokémon turns in place when
// you bump a wall); moves only if the target tile is walkable. Pure: returns a
// fresh PlayerState plus whether a step actually happened.
export function tryMove(
  map: OverworldMap,
  player: PlayerState,
  dir: Dir,
): { player: PlayerState; moved: boolean } {
  const target = addVec(player.pos, DIRS[dir]);
  if (isWalkable(map, target.x, target.y)) {
    return { player: { pos: target, facing: dir }, moved: true };
  }
  return { player: { pos: player.pos, facing: dir }, moved: false };
}

export function nodeAt(map: OverworldMap, x: number, y: number): MapNode | null {
  return map.nodes[`${x},${y}`] ?? null;
}

// The node the player is currently facing (the one an action press would open),
// or null if they're not facing a node.
export function interact(map: OverworldMap, player: PlayerState): MapNode | null {
  const f = addVec(player.pos, DIRS[player.facing]);
  return nodeAt(map, f.x, f.y);
}

// Camera offset (in px) that centers the player in the viewport, clamped so the
// camera never scrolls past the edges of the map. If the map is smaller than the
// viewport the offset pins to 0.
export function cameraOffset(
  player: PlayerState,
  view: ViewSize,
  map: OverworldMap,
  tile: number,
): Vec {
  const worldW = map.width * tile;
  const worldH = map.height * tile;
  const centerX = player.pos.x * tile + tile / 2;
  const centerY = player.pos.y * tile + tile / 2;
  const ox = clamp(view.w / 2 - centerX, Math.min(0, view.w - worldW), 0);
  const oy = clamp(view.h / 2 - centerY, Math.min(0, view.h - worldH), 0);
  return { x: Math.round(ox), y: Math.round(oy) };
}

// ---------------------------------------------------------------------------
// Sample overworld — a small starter realm so /world is playable immediately.
// Built in code (not ASCII) to avoid off-by-one authoring bugs. Real maps can be
// authored in Tiled and loaded later; this proves the engine end to end.
// ---------------------------------------------------------------------------
export function createSampleOverworld(): OverworldMap {
  const width = 16;
  const height = 12;
  const tiles: TileKind[][] = [];
  for (let y = 0; y < height; y++) {
    const row: TileKind[] = [];
    for (let x = 0; x < width; x++) row.push("grass");
    tiles.push(row);
  }

  // Tree border (the "edge of the world").
  for (let x = 0; x < width; x++) {
    tiles[0][x] = "tree";
    tiles[height - 1][x] = "tree";
  }
  for (let y = 0; y < height; y++) {
    tiles[y][0] = "tree";
    tiles[y][width - 1] = "tree";
  }

  // A pond.
  for (let y = 7; y <= 9; y++) for (let x = 2; x <= 4; x++) tiles[y][x] = "water";
  // Flower patches.
  tiles[2][6] = "flower";
  tiles[2][7] = "flower";
  tiles[3][6] = "flower";
  tiles[9][12] = "flower";
  tiles[9][13] = "flower";
  // A couple of inner trees for shape.
  tiles[4][11] = "tree";
  tiles[8][6] = "tree";

  // Paths: one horizontal spine + one vertical spine that cross.
  for (let x = 1; x < width - 1; x++) tiles[6][x] = "path";
  for (let y = 1; y < height - 1; y++) tiles[y][8] = "path";

  // Topic stages (each becomes a blocked "node" tile you face to enter).
  const nodeList: MapNode[] = [
    { id: "two-sum", x: 8, y: 2, label: "Two Sum", topic: "two sum leetcode problem", state: "next" },
    { id: "hashmaps", x: 3, y: 6, label: "Hashmaps", topic: "hashmaps and hashing", state: "locked" },
    { id: "recursion", x: 13, y: 6, label: "Recursion", topic: "recursion fundamentals", state: "locked" },
    { id: "keep", x: 8, y: 10, label: "Dragon's Keep", topic: "algorithms boss review", state: "goal" },
  ];
  const nodes: Record<string, MapNode> = {};
  for (const n of nodeList) {
    tiles[n.y][n.x] = "node";
    nodes[`${n.x},${n.y}`] = n;
  }

  // Start on the vertical path, a couple tiles below center.
  return { width, height, tiles, nodes, start: { x: 8, y: 8 } };
}
