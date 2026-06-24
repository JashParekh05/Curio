import { describe, it, expect, afterEach, vi } from "vitest";
import {
  persistGameSession,
  restoreGameSession,
  clearGameSession,
  gameSessionKey,
  type GameSessionState,
} from "./game-progress";

// A minimal-but-complete valid session used by the edge tests below.
function makeSession(sessionId: string): GameSessionState {
  return {
    version: 1,
    session_id: sessionId,
    goal: "Quantum Computing",
    current_node: "Linear Algebra",
    path: ["Quantum Computing", "Linear Algebra"],
    floor_node: "Linear Algebra",
    phase: "node-delivery",
    xp: 120,
    level: 3,
    nodes: {
      "Quantum Computing": {
        node: "Quantum Computing",
        state: "NEXT",
        is_goal: true,
      },
      "Linear Algebra": {
        node: "Linear Algebra",
        state: "CLEARED",
        is_goal: false,
      },
    },
    active_probe: null,
    active_node: null,
    last_decision: null,
    updated_at: 1_700_000_000_000,
  };
}

describe("game-progress storage-unavailable fallback (Req 21.4)", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("continues the session in memory when localStorage.setItem and getItem throw", () => {
    // Simulate private-mode / quota-exhausted storage: every read and write
    // throws. The codec must swallow the error and fall back to the in-memory
    // store so the game loop never breaks and no error reaches the learner.
    const sessionId = "fallback-throw-session";
    const setItem = vi
      .spyOn(Storage.prototype, "setItem")
      .mockImplementation(() => {
        throw new DOMException("QuotaExceededError");
      });
    const getItem = vi
      .spyOn(Storage.prototype, "getItem")
      .mockImplementation(() => {
        throw new DOMException("SecurityError");
      });

    const session = makeSession(sessionId);

    // Persist must not throw even though setItem throws.
    expect(() => persistGameSession(session)).not.toThrow();
    expect(setItem).toHaveBeenCalledWith(
      gameSessionKey(sessionId),
      JSON.stringify(session),
    );

    // Restore must not throw even though getItem throws, and must return the
    // in-memory copy rather than null.
    let restored: GameSessionState | null = null;
    expect(() => {
      restored = restoreGameSession(sessionId);
    }).not.toThrow();
    expect(getItem).toHaveBeenCalledWith(gameSessionKey(sessionId));

    // The session survives entirely in memory with all progress intact.
    expect(restored).not.toBeNull();
    expect(restored).toEqual(session);

    // Cleanup must also stay silent while storage is throwing.
    expect(() => clearGameSession(sessionId)).not.toThrow();
  });

  it("returns a deep copy from the in-memory fallback (no shared references)", () => {
    // When storage is unavailable the fallback must behave like a real
    // localStorage round-trip: mutating the restored object must not corrupt
    // the stored copy.
    const sessionId = "fallback-clone-session";
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("unavailable");
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("unavailable");
    });

    const session = makeSession(sessionId);
    persistGameSession(session);

    const first = restoreGameSession(sessionId);
    expect(first).not.toBeNull();
    // Mutate the restored copy.
    first!.xp = -999;
    first!.path.push("tampered");

    const second = restoreGameSession(sessionId);
    expect(second).toEqual(session);
    expect(second!.xp).toBe(session.xp);
    expect(second!.path).toEqual(session.path);

    clearGameSession(sessionId);
  });
});

// ---------------------------------------------------------------------------
// Phase 2 route persistence round-trip (Req 17.1, 17.2)
// ---------------------------------------------------------------------------
//
// The chosen route (`world_route`) and the optional node->world map (`worlds`)
// are additive, optional fields on GameSessionState. Two guarantees are pinned
// here against a real localStorage round-trip:
//
//   1. A session carrying `world_route` (and optionally `worlds`) persists via
//      `persistGameSession` and restores via `restoreGameSession` with the route
//      intact (Req 17.1, 17.2).
//   2. An OLDER persisted session that predates these fields still restores fine
//      with the optional fields left `undefined` (backward compatibility).

describe("game-progress route persistence round-trip (Req 17.1, 17.2)", () => {
  afterEach(() => {
    window.localStorage.clear();
  });

  it("round-trips world_route and worlds through localStorage with the route intact", () => {
    const sessionId = "route-roundtrip-session";
    const session: GameSessionState = {
      ...makeSession(sessionId),
      world_route: ["Quantum Computing", "Linear Algebra", "Vectors"],
      worlds: {
        "Quantum Computing": "dragons-keep",
        "Linear Algebra": "candyland",
        Vectors: "forest",
      },
    };

    persistGameSession(session);

    const restored = restoreGameSession(sessionId);
    expect(restored).not.toBeNull();
    // The whole shape — including the chosen route — survives the round-trip.
    expect(restored).toEqual(session);
    expect(restored!.world_route).toEqual([
      "Quantum Computing",
      "Linear Algebra",
      "Vectors",
    ]);
    expect(restored!.worlds).toEqual(session.worlds);

    clearGameSession(sessionId);
  });

  it("round-trips world_route even when worlds is omitted", () => {
    const sessionId = "route-only-session";
    const session: GameSessionState = {
      ...makeSession(sessionId),
      world_route: ["Quantum Computing", "Linear Algebra"],
    };

    persistGameSession(session);

    const restored = restoreGameSession(sessionId);
    expect(restored).not.toBeNull();
    expect(restored!.world_route).toEqual([
      "Quantum Computing",
      "Linear Algebra",
    ]);
    // `worlds` was never set and must stay absent after the round-trip.
    expect(restored!.worlds).toBeUndefined();
    expect(restored).toEqual(session);

    clearGameSession(sessionId);
  });

  it("restores an older session that predates world_route/worlds (backward compatible)", () => {
    // A session created by an earlier build of the codec — it has none of the
    // Phase 2 fields. `makeSession` returns exactly this older shape.
    const sessionId = "legacy-no-route-session";
    const legacy = makeSession(sessionId);
    expect("world_route" in legacy).toBe(false);
    expect("worlds" in legacy).toBe(false);

    persistGameSession(legacy);

    const restored = restoreGameSession(sessionId);
    expect(restored).not.toBeNull();
    // The legacy session restores intact, with the new optional fields absent.
    expect(restored).toEqual(legacy);
    expect(restored!.world_route).toBeUndefined();
    expect(restored!.worlds).toBeUndefined();

    clearGameSession(sessionId);
  });

  it("restores a raw older persisted blob (written without the Phase 2 fields) without error", () => {
    // Simulate a durable localStorage entry written by an older app version:
    // raw JSON that simply never had `world_route`/`worlds`. The decoder must
    // accept it and return the session unchanged.
    const sessionId = "legacy-raw-blob-session";
    const legacy = makeSession(sessionId);
    window.localStorage.setItem(
      gameSessionKey(sessionId),
      JSON.stringify(legacy),
    );

    let restored: GameSessionState | null = null;
    expect(() => {
      restored = restoreGameSession(sessionId);
    }).not.toThrow();
    expect(restored).toEqual(legacy);
    expect(restored!.world_route).toBeUndefined();

    clearGameSession(sessionId);
  });
});
