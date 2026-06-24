// # Feature: overworld-engine, Property 1: movement is grid-locked and respects collision
// # Feature: overworld-engine, Property 2: interaction targets the faced node only
// # Feature: overworld-engine, Property 3: camera centers the player but never scrolls past the map edge

import { describe, it, expect } from "vitest";
import {
  createSampleOverworld,
  isWalkable,
  tryMove,
  interact,
  nodeAt,
  cameraOffset,
  type PlayerState,
} from "./overworld";

const TILE = 40;
const VIEW = { w: 11 * TILE, h: 9 * TILE };

describe("overworld engine", () => {
  const map = createSampleOverworld();

  it("sample map is well-formed and starts on a walkable tile", () => {
    expect(map.width).toBe(16);
    expect(map.height).toBe(12);
    expect(isWalkable(map, map.start.x, map.start.y)).toBe(true);
    // Every registered node sits on a blocked 'node' tile.
    for (const n of Object.values(map.nodes)) {
      expect(map.tiles[n.y][n.x]).toBe("node");
      expect(isWalkable(map, n.x, n.y)).toBe(false);
      expect(nodeAt(map, n.x, n.y)?.id).toBe(n.id);
    }
  });

  it("isWalkable: bounds + blocked kinds", () => {
    expect(isWalkable(map, -1, 0)).toBe(false); // out of bounds
    expect(isWalkable(map, 0, 0)).toBe(false); // border tree
    expect(isWalkable(map, 8, 8)).toBe(true); // path (start)
    expect(isWalkable(map, 8, 2)).toBe(false); // a node
  });

  it("tryMove steps into open tiles and always sets facing", () => {
    const start: PlayerState = { pos: { x: 8, y: 8 }, facing: "down" };
    const up = tryMove(map, start, "up");
    expect(up.moved).toBe(true);
    expect(up.player.pos).toEqual({ x: 8, y: 7 });
    expect(up.player.facing).toBe("up");
  });

  it("tryMove bumps (no move) into a blocked tile but still turns", () => {
    // Standing just below the Two Sum node (8,2), facing it.
    const p: PlayerState = { pos: { x: 8, y: 3 }, facing: "down" };
    const up = tryMove(map, p, "up");
    expect(up.moved).toBe(false);
    expect(up.player.pos).toEqual({ x: 8, y: 3 }); // didn't move
    expect(up.player.facing).toBe("up"); // but turned
  });

  it("interact returns the faced node, else null", () => {
    const facingNode: PlayerState = { pos: { x: 8, y: 3 }, facing: "up" };
    expect(interact(map, facingNode)?.id).toBe("two-sum");

    const facingEmpty: PlayerState = { pos: { x: 8, y: 8 }, facing: "up" };
    expect(interact(map, facingEmpty)).toBeNull();
  });

  it("cameraOffset centers the player and clamps to map edges", () => {
    // Near the top-left corner: centering would push the camera positive, so it
    // clamps to (0,0) — the map edge stays flush with the viewport.
    const corner: PlayerState = { pos: { x: 1, y: 1 }, facing: "down" };
    expect(cameraOffset(corner, VIEW, map, TILE)).toEqual({ x: 0, y: 0 });

    // Mid-map: offset is negative (world slid up/left) and within bounds.
    const mid: PlayerState = { pos: { x: 8, y: 8 }, facing: "down" };
    const cam = cameraOffset(mid, VIEW, map, TILE);
    const worldW = map.width * TILE;
    const worldH = map.height * TILE;
    expect(cam.x).toBeLessThanOrEqual(0);
    expect(cam.x).toBeGreaterThanOrEqual(VIEW.w - worldW);
    expect(cam.y).toBeLessThanOrEqual(0);
    expect(cam.y).toBeGreaterThanOrEqual(VIEW.h - worldH);
  });
});
