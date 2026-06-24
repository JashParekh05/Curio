"use client";

// Cross-session "player profile" stats that power the Game Home Hub: the daily
// streak (the single biggest retention lever — see research: a 7-day streak makes
// a learner ~3.6x more likely to continue), a lifetime XP total, a quest counter,
// and the last topic the learner studied.
//
// This mirrors the codec discipline used elsewhere in the app
// (lib/game-progress.ts, lib/guest-progress.ts): every read/write is wrapped in
// try/catch, never throws to the caller, is guarded by `typeof window` for SSR,
// and falls back to a module-level in-memory store when localStorage is
// unavailable (private mode, quota, SSR). The hub must always render, so a
// storage failure degrades to sensible defaults rather than breaking the page.

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface PlayerStats {
  // Consecutive calendar days with at least one learning activity.
  streak: number;
  // Local-calendar day (YYYY-MM-DD) of the most recent recorded activity, or
  // null if the learner has never been active.
  lastActiveDay: string | null;
  // Cumulative XP earned across all quests. The per-quest engine owns its own
  // running XP in GameSessionState; this is the durable lifetime aggregate the
  // hub's level ring reads from.
  lifetimeXp: number;
  // How many quests the learner has started (a light vanity/progress metric).
  questsStarted: number;
  // The most recently entered topic, used for the hub's "jump back in" copy.
  lastTopic: string | null;
}

export function defaultPlayerStats(): PlayerStats {
  return {
    streak: 0,
    lastActiveDay: null,
    lifetimeXp: 0,
    questsStarted: 0,
    lastTopic: null,
  };
}

// ---------------------------------------------------------------------------
// Pure date + streak helpers (exported for testing)
// ---------------------------------------------------------------------------

// Format a Date as a LOCAL-calendar YYYY-MM-DD string. We build it from the
// local getFullYear/getMonth/getDate (not toISOString, which is UTC) so the
// "day" boundary matches the learner's wall clock — otherwise a late-evening
// session could be attributed to the wrong day and break a streak.
export function todayISO(d: Date = new Date()): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

// Whole-day difference between two YYYY-MM-DD strings (b - a), in local days.
// Returns a positive integer when `b` is after `a`. Parsed at local noon to be
// safe from DST edges.
export function daysBetween(a: string, b: string): number {
  const pa = parseLocalDay(a);
  const pb = parseLocalDay(b);
  if (pa == null || pb == null) return NaN;
  const MS_PER_DAY = 24 * 60 * 60 * 1000;
  return Math.round((pb - pa) / MS_PER_DAY);
}

function parseLocalDay(iso: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return null;
  const [, y, mo, d] = m;
  // Noon local avoids DST/midnight rounding surprises in the day delta.
  return new Date(Number(y), Number(mo) - 1, Number(d), 12, 0, 0, 0).getTime();
}

// Given the previous streak + last active day and "today", compute the new
// streak after recording an activity today. Pure and total:
//   - same day as last activity  -> streak unchanged (already counted today)
//   - exactly the next day       -> streak + 1 (continued)
//   - any other gap (or first ever / unparseable) -> streak resets to 1
export function nextStreak(
  prevStreak: number,
  lastActiveDay: string | null,
  today: string,
): number {
  const safePrev = Number.isFinite(prevStreak) ? Math.max(0, Math.floor(prevStreak)) : 0;
  if (!lastActiveDay) return 1;
  const gap = daysBetween(lastActiveDay, today);
  if (!Number.isFinite(gap)) return 1;
  if (gap <= 0) return Math.max(1, safePrev); // same day (or clock skew) — keep
  if (gap === 1) return safePrev + 1; // consecutive day — extend
  return 1; // missed one or more days — reset
}

// Has the learner already been active today (so the streak is "secured")?
export function isActiveToday(stats: PlayerStats, today: string = todayISO()): boolean {
  return stats.lastActiveDay === today;
}

// ---------------------------------------------------------------------------
// Storage
// ---------------------------------------------------------------------------

const KEY = "curio_player_stats_v1";

let memoryStats: PlayerStats | null = null;

function sanitize(raw: unknown): PlayerStats {
  const base = defaultPlayerStats();
  if (!raw || typeof raw !== "object") return base;
  const r = raw as Record<string, unknown>;
  return {
    streak: typeof r.streak === "number" && r.streak >= 0 ? Math.floor(r.streak) : 0,
    lastActiveDay: typeof r.lastActiveDay === "string" ? r.lastActiveDay : null,
    lifetimeXp: typeof r.lifetimeXp === "number" && r.lifetimeXp >= 0 ? Math.floor(r.lifetimeXp) : 0,
    questsStarted:
      typeof r.questsStarted === "number" && r.questsStarted >= 0 ? Math.floor(r.questsStarted) : 0,
    lastTopic: typeof r.lastTopic === "string" ? r.lastTopic : null,
  };
}

export function getPlayerStats(): PlayerStats {
  if (typeof window === "undefined") {
    return memoryStats ? { ...memoryStats } : defaultPlayerStats();
  }
  try {
    const rawStr = localStorage.getItem(KEY);
    if (rawStr != null) return sanitize(JSON.parse(rawStr));
  } catch {
    // fall through to in-memory / defaults
  }
  return memoryStats ? { ...memoryStats } : defaultPlayerStats();
}

function savePlayerStats(stats: PlayerStats): void {
  memoryStats = { ...stats };
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(KEY, JSON.stringify(stats));
  } catch {
    /* keep the in-memory copy */
  }
}

// ---------------------------------------------------------------------------
// Mutations (best-effort, never throw)
// ---------------------------------------------------------------------------

// Record that the learner did something educational today, advancing the streak
// per `nextStreak`. Idempotent within a single day. Returns the updated stats.
export function recordActiveToday(now: Date = new Date()): PlayerStats {
  const today = todayISO(now);
  const prev = getPlayerStats();
  const updated: PlayerStats = {
    ...prev,
    streak: nextStreak(prev.streak, prev.lastActiveDay, today),
    lastActiveDay: today,
  };
  savePlayerStats(updated);
  return updated;
}

// Record the start of a new quest: advances the streak (activity today), bumps
// the quest counter, and remembers the topic for "jump back in" copy.
export function recordQuestStart(topic: string, now: Date = new Date()): PlayerStats {
  const afterStreak = recordActiveToday(now);
  const updated: PlayerStats = {
    ...afterStreak,
    questsStarted: afterStreak.questsStarted + 1,
    lastTopic: topic.trim() || afterStreak.lastTopic,
  };
  savePlayerStats(updated);
  return updated;
}

// Add to the lifetime XP aggregate (called by the play loop when XP is awarded).
// Clamps non-positive deltas to a no-op read so callers can pass any value.
export function addLifetimeXp(delta: number, current: PlayerStats = getPlayerStats()): PlayerStats {
  if (!Number.isFinite(delta) || delta <= 0) return current;
  const updated: PlayerStats = { ...current, lifetimeXp: current.lifetimeXp + Math.floor(delta) };
  savePlayerStats(updated);
  return updated;
}
