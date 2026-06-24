"use client";

// BattleScreen — the retro pixel-art reskin of `OutcomeCard` (Req 5.1, 5.2,
// 5.3, 5.4, 11.1, 11.2).
//
// Where `OutcomeCard` drew three brutalist roguelike result cards, the
// BattleScreen renders the SAME banded decision as an arcade battle result
// against the Stage's Monster, styled with the `--pq-*` retro tokens scoped
// under `.pixel-quest` (see `frontend/app/retro.css`). It is a pure reskin — it
// preserves the EXACT props/data contract of `OutcomeCard` (`decision`,
// `onContinue?`, `compact?`, `className?`) so the Game_State_Machine wiring in
// `play/page.tsx` only swaps the import (Req 11.2).
//
// The three bands map to battle framings (Req 5.1):
//   - CLIMB     → "Monster slain!" — the Knight advances (gold)
//   - REINFORCE → "Stalemate — hold the line" — re-quiz the same Stage (cyan)
//   - DESCEND   → "Tactical retreat — train up" — a strategic retreat to gear
//                 up, framed explicitly NOT as a failure or loss (Req 5.3)
//   - CLIMB + reached_goal → "The Dragon falls!" — the quest is complete (Req 5.4)
//
// The `decide_next` diagnosis is shown prominently on whichever result renders
// (Req 5.2). On mount the result plays a brief screen-shake clash; that is
// animation, so it is gated by `useRetroSettings().reducedMotion` — under
// reduced motion the result renders statically with no shake (Req 1.4, 12.1).

import type { Band, DecideResponse } from "@/lib/game-progress";

import { useRetroSettings } from "./RetroThemeProvider";

// ---------------------------------------------------------------------------
// Per-band presentation (pure config)
// ---------------------------------------------------------------------------
//
// Each band maps to a fixed battle framing + retro accent token. Copy is
// adventure-framed: the DESCEND variant is a "tactical retreat to train up"
// beat, never a "you failed" beat (Req 5.3).

interface BandPresentation {
  // Retro accent token (a `--pq-*` color) used for the result plate.
  accent: string;
  // Pixel-art emblem matching the battle outcome.
  emblem: string;
  // Short eyebrow label (the "result type").
  eyebrow: string;
  // Punchy battle headline.
  title: string;
  // One-line adventure framing shown under the headline.
  tagline: string;
  // Label for the continue action.
  cta: string;
}

const PRESENTATION: Record<Band, BandPresentation> = {
  CLIMB: {
    accent: "var(--pq-gold)",
    emblem: "⚔️",
    eyebrow: "Victory",
    title: "Monster slain!",
    tagline: "You bested the guardian. The trail onward opens — advance, knight.",
    cta: "Advance",
  },
  REINFORCE: {
    accent: "var(--pq-cyan)",
    emblem: "🛡️",
    eyebrow: "Stalemate",
    title: "Stalemate — hold the line",
    tagline: "A close clash. Hold your ground and strike this Stage once more.",
    cta: "Hold the line",
  },
  DESCEND: {
    accent: "var(--pq-orange)",
    emblem: "🏕️",
    eyebrow: "Retreat",
    title: "Tactical retreat — train up",
    tagline:
      "Not a loss — a wise march back to train up. Sharpen a fundamental and the keep falls easier.",
    cta: "Retreat & train",
  },
};

// The goal-reached battle: the final boss falls (Req 5.4).
const DRAGON_FALLS: Pick<BandPresentation, "emblem" | "eyebrow" | "title" | "tagline" | "cta"> = {
  emblem: "🐉",
  eyebrow: "Quest complete",
  title: "The Dragon falls!",
  tagline: "Keep cleared — you slew the Dragon and claimed the topic. The quest is won.",
  cta: "Finish",
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface BattleScreenProps {
  // The banded next-step decision returned by `/api/game/decide`. Carries the
  // band/action, the `diagnosis`, and the supporting `gap` / `next_node`.
  decision: DecideResponse;
  // Optional handler for the continue/advance action. When omitted, the CTA is
  // not rendered (e.g. when the parent drives the transition some other way).
  onContinue?: () => void;
  // Optional compact variant for tight surfaces (e.g. over a clip cutscene).
  compact?: boolean;
  // Optional extra classes for layout composition by the parent surface.
  className?: string;
}

/**
 * Renders exactly one of the retro battle results (Monster slain / stalemate /
 * tactical retreat, or the Dragon's defeat at the goal) for a graded quiz
 * `decision`, with the `decide_next` diagnosis shown prominently (Req 5.1–5.4).
 * A brief screen-shake plays on mount unless reduced motion is requested.
 * Presentational only.
 */
export default function BattleScreen({
  decision,
  onContinue,
  compact = false,
  className = "",
}: BattleScreenProps) {
  const { reducedMotion } = useRetroSettings();

  // The code-computed band is the source of truth for which result to show;
  // fall back to the LLM `action` only if `band` is somehow absent.
  const band: Band = decision.band ?? decision.action;
  const base = PRESENTATION[band] ?? PRESENTATION.REINFORCE;
  const reachedGoal = band === "CLIMB" && decision.reached_goal;

  // When the goal is reached on a CLIMB, the Dragon falls — override the copy
  // and emblem while keeping the victory (gold) accent (Req 5.4).
  const p = reachedGoal ? { ...base, ...DRAGON_FALLS } : base;

  const diagnosis = decision.diagnosis?.trim() ?? "";
  const gap = decision.gap?.trim() ?? "";
  const nextNode = decision.next_node?.trim() ?? "";

  return (
    <section
      // The shake is the battle clash; only attach it when motion is allowed so
      // reduced motion renders the result statically (Req 1.4, 12.1). retro.css
      // also hard-disables `.pq-shake` under reduced motion as a fallback.
      className={`pixelated relative${reducedMotion ? "" : " pq-shake"} ${className}`}
      role="status"
      aria-live="polite"
      aria-label={`Battle outcome: ${p.eyebrow}`}
      data-band={band}
      data-reached-goal={reachedGoal ? "true" : "false"}
      style={{
        background: "var(--pq-panel)",
        color: "var(--pq-text)",
        border: "4px solid var(--pq-ink)",
        boxShadow: "6px 6px 0 0 var(--pq-ink)",
        padding: compact ? "0.85rem 1rem" : "1.1rem 1.25rem",
      }}
    >
      {/* Eyebrow + emblem on the band-colored result plate. */}
      <div className="flex items-center" style={{ gap: "0.6rem", marginBottom: "0.7rem" }}>
        <span
          aria-hidden
          className="pixelated"
          style={{ fontSize: compact ? "1.4rem" : "1.8rem", lineHeight: 1 }}
        >
          {p.emblem}
        </span>
        <span
          className="pixel-font pixelated"
          style={{
            background: p.accent,
            color: "var(--pq-ink)",
            border: "3px solid var(--pq-ink)",
            padding: "0.25rem 0.5rem",
            fontSize: "0.45rem",
            textTransform: "uppercase",
            letterSpacing: "0.14em",
          }}
        >
          {p.eyebrow}
        </span>
      </div>

      {/* Battle headline + adventure framing. */}
      <h2
        className="pixel-font"
        style={{
          color: p.accent,
          fontSize: compact ? "0.85rem" : "1.05rem",
          lineHeight: 1.5,
          margin: 0,
        }}
      >
        {p.title}
      </h2>
      <p
        className="pixel-body"
        style={{
          margin: "0.5rem 0 0",
          fontSize: compact ? "0.95rem" : undefined,
          opacity: 0.92,
        }}
      >
        {p.tagline}
      </p>

      {/* Diagnosis — the `decide_next` explanation, shown prominently (Req 5.2).
          Rendered on a parchment plate so it reads as the battle report. */}
      <div
        className="pixelated"
        style={{
          background: "var(--pq-paper)",
          color: "var(--pq-ink)",
          border: "3px solid var(--pq-ink)",
          padding: "0.65rem 0.75rem",
          marginTop: "0.85rem",
        }}
      >
        <span
          className="pixel-font"
          style={{
            display: "block",
            fontSize: "0.4rem",
            textTransform: "uppercase",
            letterSpacing: "0.14em",
            opacity: 0.7,
            marginBottom: "0.4rem",
          }}
        >
          Diagnosis
        </span>
        {diagnosis ? (
          <p className="pixel-body" style={{ margin: 0, color: "var(--pq-ink)", fontSize: compact ? "0.95rem" : undefined }}>
            {diagnosis}
          </p>
        ) : (
          <p
            className="pixel-body"
            style={{ margin: 0, color: "var(--pq-ink)", opacity: 0.55, fontStyle: "italic", fontSize: compact ? "0.95rem" : undefined }}
          >
            No diagnosis available for this outcome.
          </p>
        )}
      </div>

      {/* Supporting beats: the gap to close and the Stage the Knight moves to
          next. Hidden on a goal-reached result (the quest is over). */}
      {(gap || (nextNode && !reachedGoal)) && (
        <dl className="flex flex-wrap" style={{ gap: "0.5rem", marginTop: "0.85rem" }}>
          {gap && (
            <div
              className="pixelated min-w-0"
              style={{
                background: "var(--pq-bg)",
                border: "3px solid var(--pq-ink)",
                padding: "0.4rem 0.55rem",
              }}
            >
              <dt
                className="pixel-font"
                style={{ fontSize: "0.38rem", textTransform: "uppercase", letterSpacing: "0.14em", opacity: 0.6 }}
              >
                Gap
              </dt>
              <dd
                className="pixel-font truncate"
                style={{ margin: 0, fontSize: "0.5rem", maxWidth: "60vw" }}
              >
                {gap}
              </dd>
            </div>
          )}
          {nextNode && !reachedGoal && (
            <div
              className="pixelated min-w-0"
              style={{
                background: "var(--pq-bg)",
                border: "3px solid var(--pq-ink)",
                padding: "0.4rem 0.55rem",
              }}
            >
              <dt
                className="pixel-font"
                style={{ fontSize: "0.38rem", textTransform: "uppercase", letterSpacing: "0.14em", opacity: 0.6 }}
              >
                {band === "DESCEND" ? "Fall back to" : "Onward to"}
              </dt>
              <dd
                className="pixel-font truncate"
                style={{ margin: 0, fontSize: "0.5rem", maxWidth: "60vw" }}
              >
                {nextNode}
              </dd>
            </div>
          )}
        </dl>
      )}

      {/* Continue action — labeled per band so the verb matches the beat. */}
      {onContinue && (
        <div style={{ marginTop: "1rem" }}>
          <button
            type="button"
            onClick={onContinue}
            className="pixel-font pixelated"
            style={{
              background: "var(--pq-ink)",
              color: "var(--pq-text)",
              border: "3px solid var(--pq-ink)",
              boxShadow: "3px 3px 0 0 var(--pq-gold)",
              padding: compact ? "0.5rem 0.85rem" : "0.65rem 1.1rem",
              fontSize: "0.55rem",
              textTransform: "uppercase",
              letterSpacing: "0.08em",
              cursor: "pointer",
            }}
          >
            {p.cta}
          </button>
        </div>
      )}
    </section>
  );
}
