"use client";

// XP/Level HUD — the gamification status bar for the Play_Surface (Req 20.1).
//
// When a learner completes a Node quiz the Play_Surface awards XP and updates
// this HUD (Req 20.1). This component is purely presentational and prop-driven:
// the Game_State_Machine owns the running `xp` total (persisted in
// GameSessionState, see lib/game-progress.ts) and passes it down here. The HUD
// renders an XP bar + current level and, when the XP total increases between
// renders, briefly flashes a "+N XP" award increment so the gain reads as a
// reward beat rather than a silent number change.
//
// Per the design's "Medieval pixel-art adventure UX" (Req 22.7, 23): interactive
// chrome stays brutalist — `.brutal-card` / `.brutal` panels, thick ink border,
// flat accent fills, hard shadow — so the HUD reads as "UI on top of the world".

import { useEffect, useRef, useState } from "react";

// ---------------------------------------------------------------------------
// Level derivation (pure helpers)
// ---------------------------------------------------------------------------
//
// XP maps to a Level via a mild growth curve: each level costs a little more XP
// than the previous one, so early levels come quickly and later levels feel
// earned. The cost to advance FROM level L (1-indexed) TO level L+1 is
//   BASE_LEVEL_XP + (L - 1) * LEVEL_XP_STEP
// i.e. 100 XP for level 1→2, 150 for 2→3, 200 for 3→4, and so on. The curve is
// deterministic and total over all real `xp` inputs (negatives and fractions
// are clamped/floored), which keeps the HUD safe to render from any session
// state.

export const BASE_LEVEL_XP = 100;
export const LEVEL_XP_STEP = 50;

// XP cost to advance from `level` (>= 1) to the next level.
function costToAdvance(level: number): number {
  const l = Math.max(1, Math.floor(level));
  return BASE_LEVEL_XP + (l - 1) * LEVEL_XP_STEP;
}

// Stats describing where a given XP total sits relative to its level.
export interface XpLevelStats {
  level: number; // current level (>= 1)
  xpIntoLevel: number; // XP accumulated within the current level
  xpForLevel: number; // total XP needed to clear the current level
  xpToNextLevel: number; // XP remaining until the next level
  progress: number; // fraction within the current level, in [0, 1]
}

/**
 * Derive level + intra-level progress from a cumulative XP total. Pure, total,
 * and deterministic: clamps negatives to 0 and floors fractional XP so it is
 * safe to call with any value from session state.
 */
export function xpLevelStats(xp: number): XpLevelStats {
  const safeXp = Number.isFinite(xp) ? Math.max(0, Math.floor(xp)) : 0;

  let level = 1;
  let remaining = safeXp;
  // Walk up levels while the remaining XP covers the next level's cost.
  while (remaining >= costToAdvance(level)) {
    remaining -= costToAdvance(level);
    level += 1;
  }

  const xpForLevel = costToAdvance(level);
  const xpIntoLevel = remaining;
  const xpToNextLevel = Math.max(0, xpForLevel - xpIntoLevel);
  const progress = xpForLevel > 0 ? xpIntoLevel / xpForLevel : 0;

  return { level, xpIntoLevel, xpForLevel, xpToNextLevel, progress };
}

/** Convenience: just the derived level for a given XP total. */
export function levelForXp(xp: number): number {
  return xpLevelStats(xp).level;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface XpHudProps {
  // The learner's cumulative XP total (owned/persisted by the state machine).
  xp: number;
  // Optional explicit level. When omitted, the level is derived from `xp` via
  // `xpLevelStats`. Supplying it lets the parent override the derived value if
  // it tracks level separately (GameSessionState carries both xp and level).
  level?: number;
  // Optional compact variant for tight surfaces (e.g. over a clip overlay).
  compact?: boolean;
  // Optional extra classes for layout composition by the parent surface.
  className?: string;
}

/**
 * XP/level HUD: an XP bar + current level, with a transient "+N XP" award
 * increment that flashes whenever the XP total rises (Req 20.1). Presentational
 * only — the parent awards XP on Node-quiz completion and passes the new total.
 */
export default function XpHud({
  xp,
  level,
  compact = false,
  className = "",
}: XpHudProps) {
  const safeXp = Number.isFinite(xp) ? Math.max(0, Math.floor(xp)) : 0;
  const stats = xpLevelStats(safeXp);
  // Prefer an explicit level when provided; otherwise use the derived one.
  const shownLevel =
    typeof level === "number" && Number.isFinite(level)
      ? Math.max(1, Math.floor(level))
      : stats.level;

  // Track the previous XP total so we can surface the delta as an award beat.
  const prevXpRef = useRef<number>(safeXp);
  const [award, setAward] = useState<number | null>(null);

  useEffect(() => {
    const prev = prevXpRef.current;
    if (safeXp > prev) {
      // XP went up — flash the gained amount, then clear it after a beat.
      setAward(safeXp - prev);
      const t = setTimeout(() => setAward(null), 1400);
      prevXpRef.current = safeXp;
      return () => clearTimeout(t);
    }
    // XP unchanged or reset (e.g. new session) — sync without flashing.
    prevXpRef.current = safeXp;
  }, [safeXp]);

  // Clamp the rendered bar width to [0, 100]% for safety.
  const pct = Math.round(Math.min(1, Math.max(0, stats.progress)) * 100);

  return (
    <section
      className={`brutal-card relative bg-paper text-ink ${
        compact ? "px-3 py-2" : "px-4 py-3 sm:px-5 sm:py-4"
      } ${className}`}
      aria-label="Experience and level"
    >
      <div className="flex items-center gap-3">
        {/* Level badge — the medieval-adventure "rank" chip. */}
        <div
          className="brutal bg-accent-purple text-white flex flex-col items-center justify-center leading-none px-2.5 py-1.5 shrink-0"
          aria-label={`Level ${shownLevel}`}
        >
          <span className="text-[8px] font-black uppercase tracking-widest opacity-80">
            Lvl
          </span>
          <span className={`font-black ${compact ? "text-base" : "text-lg"}`}>
            {shownLevel}
          </span>
        </div>

        {/* XP bar + labels. */}
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline justify-between gap-2 mb-1">
            <span className="text-ink/70 text-[10px] font-black uppercase tracking-widest">
              XP
            </span>
            <span className="text-ink/70 text-[10px] font-bold tabular-nums">
              {stats.xpIntoLevel} / {stats.xpForLevel}
            </span>
          </div>

          {/* The bar itself: a thick ink-bordered track with a flat lime fill. */}
          <div
            className="brutal bg-white h-4 w-full overflow-hidden"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={stats.xpForLevel}
            aria-valuenow={stats.xpIntoLevel}
            aria-label="XP toward next level"
          >
            <div
              className="h-full bg-accent-lime transition-[width] duration-500 ease-out"
              style={{ width: `${pct}%` }}
            />
          </div>

          {!compact && (
            <p className="text-ink/50 text-[10px] font-bold uppercase tracking-wider mt-1">
              {stats.xpToNextLevel} XP to level {shownLevel + 1}
            </p>
          )}
        </div>
      </div>

      {/* Award increment: a transient "+N XP" badge that flashes on a gain
          (Req 20.1). Rendered as an overlay so it never reflows the HUD. */}
      {award != null && award > 0 && (
        <div
          className="absolute -top-3 right-3 brutal bg-accent-yellow text-ink text-xs font-black uppercase tracking-wider px-2 py-1 animate-bounce"
          role="status"
          aria-live="polite"
        >
          +{award} XP
        </div>
      )}
    </section>
  );
}
