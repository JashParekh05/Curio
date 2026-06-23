"use client";

// localStorage codec for the Adaptive Learning Game's client-owned session state
// (Play_Surface / Game_State_Machine). Mirrors the guest-progress pattern: every
// read/write is wrapped in try/catch, never throws to the caller, and is guarded
// by `typeof window` for SSR. When localStorage is unavailable (private mode,
// quota exhausted, SSR), the session continues purely in memory via a
// module-level fallback store so the loop never breaks (Req 21.4).

// ---------------------------------------------------------------------------
// Types — mirror the backend wire contract (design: Data Models -> localStorage v1)
// ---------------------------------------------------------------------------

// A single multiple-choice question. Mirrors the backend `ProbeQuestion` schema;
// used both for the placement probe and for per-node checkpoint quizzes.
export interface ProbeQuestion {
  question: string;
  options: string[];
  correct_index: number;
  explanation: string;
  concept_tag: string; // the concept this question tests (Req 2.3, 4)
  level: string; // "prerequisite" | "core" | "stretch"
}

// A selected YouTube clip for a node. Mirrors the backend `NodeClip` schema;
// `video_url` is always the `youtube.com/embed/...` form (Req 10.3).
export interface NodeClip {
  video_id: string;
  video_url: string;
  title: string;
  channel_title: string | null;
  duration_seconds: number;
  has_caption: boolean;
  view_count: number;
  thumbnail_url: string | null;
  description: string | null;
}

// The Score_Band assigned to a graded quiz / chosen by the next-step decision.
export type Band = "DESCEND" | "REINFORCE" | "CLIMB";

// The display state of a node on the Skill_Tree_Map (Req 19.3). In Phase 1
// DECAYING/MASTERED may render without any spaced-decay scheduling (Req 19.5).
export type NodeState = "LOCKED" | "NEXT" | "CLEARED" | "DECAYING" | "MASTERED";

// The phases of the client Game_State_Machine.
export type GamePhase =
  | "topic-entry"
  | "probe"
  | "grading"
  | "decision"
  | "node-delivery"
  | "outcome";

// The banded next-step decision returned by the backend `/decide` endpoint.
export interface DecideResponse {
  action: Band;
  band: Band;
  next_node: string | null;
  reached_goal: boolean;
  gap: string;
  diagnosis: string;
  score_pct: number;
  missed_concepts: string[];
  correct_concepts: string[];
}

// A discovered node as rendered on the Skill_Tree_Map.
export interface NodeView {
  node: string;
  state: NodeState;
  is_goal: boolean;
  hook?: string;
  clip?: NodeClip;
  quiz?: ProbeQuestion[];
}

// The full client-owned session state persisted to localStorage. Keyed by
// `curio_game_session_${sessionId}`.
export interface GameSessionState {
  version: 1;
  session_id: string;
  goal: string; // Goal_Node (the entered topic)
  current_node: string;
  path: string[]; // ordered visited nodes (descent chain + climb)
  floor_node: string | null; // set on first CLIMB out of a descent (Req 6.2)
  phase: GamePhase;
  xp: number;
  level: number;
  nodes: Record<string, NodeView>; // discovered nodes for the Skill_Tree_Map
  active_probe: ProbeQuestion[] | null;
  active_node: NodeView | null;
  last_decision: DecideResponse | null;
  updated_at: number;
}

// ---------------------------------------------------------------------------
// Codec
// ---------------------------------------------------------------------------

const KEY_PREFIX = "curio_game_session_";

export function gameSessionKey(sessionId: string): string {
  return `${KEY_PREFIX}${sessionId}`;
}

// In-memory fallback store, used when localStorage is unavailable (SSR, private
// mode, quota). Keyed by the same `curio_game_session_${sessionId}` key so a
// session started in memory restores consistently within the same page life.
const memoryStore = new Map<string, GameSessionState>();

// Deep-clone via JSON so the in-memory fallback behaves the same as a real
// localStorage round-trip (no shared references leak back to the caller).
function clone(state: GameSessionState): GameSessionState {
  return JSON.parse(JSON.stringify(state)) as GameSessionState;
}

// Persist a session. Tries localStorage first; on any failure (or SSR) falls
// back to the in-memory store. Never throws.
export function persistGameSession(state: GameSessionState): void {
  const key = gameSessionKey(state.session_id);
  if (typeof window === "undefined") {
    memoryStore.set(key, clone(state));
    return;
  }
  try {
    localStorage.setItem(key, JSON.stringify(state));
    // A prior in-memory copy (from before storage became available) is now
    // superseded by the durable copy — drop it to avoid a stale read.
    memoryStore.delete(key);
  } catch {
    // storage unavailable (private mode / quota) — keep the session in memory
    memoryStore.set(key, clone(state));
  }
}

// Restore a session by id. Tries localStorage first; on miss or any failure (or
// SSR) falls back to the in-memory store. Returns null when no session exists.
// Never throws.
export function restoreGameSession(sessionId: string): GameSessionState | null {
  const key = gameSessionKey(sessionId);
  if (typeof window === "undefined") {
    const mem = memoryStore.get(key);
    return mem ? clone(mem) : null;
  }
  try {
    const raw = localStorage.getItem(key);
    if (raw != null) return JSON.parse(raw) as GameSessionState;
  } catch {
    // storage unavailable — fall through to the in-memory store
  }
  const mem = memoryStore.get(key);
  return mem ? clone(mem) : null;
}

// Remove a session from both stores. Never throws.
export function clearGameSession(sessionId: string): void {
  const key = gameSessionKey(sessionId);
  memoryStore.delete(key);
  if (typeof window === "undefined") return;
  try {
    localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

// ---------------------------------------------------------------------------
// Active-session pointer
// ---------------------------------------------------------------------------
//
// The Play_Surface persists per-session state keyed by `session_id`, but on a
// reload it needs to know WHICH session was in progress so it can restore it
// (Req 21.3). A tiny pointer key records the id of the currently active session;
// like the codec above it is fully guarded (try/catch + SSR + in-memory
// fallback) so storage being unavailable never breaks the loop (Req 21.4).

const ACTIVE_KEY = "curio_game_active_session";

let memoryActiveSessionId: string | null = null;

// Record the id of the in-progress session so a reload can restore it.
export function setActiveGameSessionId(sessionId: string): void {
  memoryActiveSessionId = sessionId;
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(ACTIVE_KEY, sessionId);
  } catch {
    /* keep the in-memory pointer */
  }
}

// Read the id of the in-progress session, or null when there is none.
export function getActiveGameSessionId(): string | null {
  if (typeof window === "undefined") return memoryActiveSessionId;
  try {
    const raw = localStorage.getItem(ACTIVE_KEY);
    if (raw != null) return raw;
  } catch {
    /* fall through to the in-memory pointer */
  }
  return memoryActiveSessionId;
}

// Clear the active-session pointer (e.g. when a session ends or is abandoned).
export function clearActiveGameSessionId(): void {
  memoryActiveSessionId = null;
  if (typeof window === "undefined") return;
  try {
    localStorage.removeItem(ACTIVE_KEY);
  } catch {
    /* ignore */
  }
}
