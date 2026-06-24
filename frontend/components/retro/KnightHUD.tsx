"use client";

// KnightHUD — the retro pixel-art reskin of `XpHud` (Req 7.1, 7.2, 11.1, 11.2).
//
// Where `XpHud` drew a brutalist XP bar + level chip, the KnightHUD renders the
// same data as a classic arcade status bar: a "rank" plate (the level) and an
// XP gauge, styled with the `--pq-*` retro tokens scoped under `.pixel-quest`
// (see `frontend/app/retro.css`). It is a pure reskin — it preserves the EXACT
// props and data contract of `XpHud` (`xp`, `level?`, `compact?`, `className?`)
// so the Game_State_Machine wiring in `play/page.tsx` only swaps the import
// (Req 11.2).
//
// The level math is NOT duplicated: this component reuses `xpLevelStats` (and
// the derived `levelForXp`) exported from `XpHud`, so the rank/XP curve stays
// the single source of truth across both surfaces.
//
// When the XP total rises between renders the HUD flashes a transient "+N XP"
// award beat so a gain reads as a reward (Req 7.2). That flash is animation, so
// it is gated by `useRetroSettings().reducedMotion`: under reduced motion the
// award still surfaces (briefly, no bounce/pulse) and then clears, honoring
// `prefers-reduced-motion` (Req 1.4, 12.1).

import { useEffect, useRef, useState } from "react";

import { xpLevelStats } from "../XpHud";
import { useRetroSettings } from "./RetroThemeProvider";

export interface KnightHUDProps {
  // The learner's cumulative XP total (owned/persisted by the state machine).
  xp: number;
  // Optional explicit level. When omitted, the level is derived from `xp` via
  // `xpLevelStats`. Supplying it lets the parent override the derived value if
  // it tracks level separately (GameSessionState carries both xp and level).
  level?: number;
  // Optional compact variant for tight surfaces (e.g. over a clip cutscene).
  compact?: boolean;
  // Optional extra classes for layout composition by the parent surface.
  className?: string;
}

/**
 * Arcade status bar: a rank plate (level) + an XP gauge, with a transient
 * "+N XP" award beat that flashes whenever the XP total rises (Req 7.2).
 * Presentational only — the parent awards XP on Node-quiz completion and passes
 * the new total. Reuses `xpLevelStats` from `XpHud` so the level curve is shared
 * (no duplicated logic).
 */
export default function KnightHUD({
  xp,
  level,
  compact = false,
  className = "",
}: KnightHUDProps) {
  const { reducedMotion } = useRetroSettings();

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

  // Clamp the rendered gauge width to [0, 100]% for safety.
  const pct = Math.round(Math.min(1, Math.max(0, stats.progress)) * 100);

  return (
    <section
      className={`pixelated relative ${className}`}
      aria-label="Knight rank and experience"
      style={{
        background: "var(--pq-panel)",
        color: "var(--pq-text)",
        border: "4px solid var(--pq-ink)",
        boxShadow: "4px 4px 0 0 var(--pq-ink)",
        padding: compact ? "0.5rem 0.65rem" : "0.7rem 0.9rem",
      }}
    >
      <div className="flex items-center" style={{ gap: compact ? "0.6rem" : "0.85rem" }}>
        {/* Rank plate — the Knight's level shown as an arcade "RANK" badge. */}
        <div
          className="pixel-font pixelated flex flex-col items-center justify-center shrink-0"
          aria-label={`Rank ${shownLevel}`}
          style={{
            background: "var(--pq-gold)",
            color: "var(--pq-ink)",
            border: "3px solid var(--pq-ink)",
            lineHeight: 1,
            padding: compact ? "0.3rem 0.45rem" : "0.4rem 0.6rem",
          }}
        >
          <span
            style={{
              fontSize: "0.4rem",
              textTransform: "uppercase",
              letterSpacing: "0.14em",
              opacity: 0.8,
            }}
          >
            Rank
          </span>
          <span style={{ fontSize: compact ? "0.85rem" : "1rem" }}>
            {shownLevel}
          </span>
        </div>

        {/* XP gauge + labels. */}
        <div className="flex-1 min-w-0">
          <div
            className="flex items-baseline justify-between"
            style={{ gap: "0.5rem", marginBottom: "0.3rem" }}
          >
            <span
              className="pixel-font"
              style={{
                fontSize: "0.45rem",
                textTransform: "uppercase",
                letterSpacing: "0.14em",
                opacity: 0.8,
              }}
            >
              XP
            </span>
            <span
              className="pixel-font tabular-nums"
              style={{ fontSize: "0.45rem", opacity: 0.8 }}
            >
              {stats.xpIntoLevel} / {stats.xpForLevel}
            </span>
          </div>

          {/* The gauge itself: an inked track with a flat `--pq-xp` cyan fill. */}
          <div
            className="pixelated w-full overflow-hidden"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={stats.xpForLevel}
            aria-valuenow={stats.xpIntoLevel}
            aria-label="XP toward next rank"
            style={{
              background: "var(--pq-bg)",
              border: "3px solid var(--pq-ink)",
              height: compact ? "0.7rem" : "0.85rem",
            }}
          >
            <div
              style={{
                height: "100%",
                width: `${pct}%`,
                background: "var(--pq-xp)",
                // Animate the fill only when motion is allowed; reduced motion
                // snaps to the resolved width (Req 1.4, 12.1).
                transition: reducedMotion ? "none" : "width 500ms ease-out",
              }}
            />
          </div>

          {!compact && (
            <p
              className="pixel-font"
              style={{
                fontSize: "0.45rem",
                textTransform: "uppercase",
                letterSpacing: "0.1em",
                opacity: 0.6,
                margin: "0.3rem 0 0",
              }}
            >
              {stats.xpToNextLevel} XP to rank {shownLevel + 1}
            </p>
          )}
        </div>
      </div>

      {/* Award beat: a transient "+N XP" badge that flashes on a gain (Req 7.2).
          Rendered as an overlay so it never reflows the HUD. The pulse is
          animation, so it is suppressed under reduced motion — the badge still
          appears (and clears) but without the bounce (Req 1.4, 12.1). */}
      {award != null && award > 0 && (
        <div
          className={`pixel-font pixelated absolute${
            reducedMotion ? "" : " animate-bounce"
          }`}
          role="status"
          aria-live="polite"
          style={{
            top: "-0.6rem",
            right: "0.6rem",
            background: "var(--pq-lime)",
            color: "var(--pq-ink)",
            border: "3px solid var(--pq-ink)",
            padding: "0.2rem 0.4rem",
            fontSize: "0.5rem",
            textTransform: "uppercase",
            letterSpacing: "0.08em",
          }}
        >
          +{award} XP
        </div>
      )}
    </section>
  );
}
