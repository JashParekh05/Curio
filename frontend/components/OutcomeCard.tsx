"use client";

// Outcome cards — the three roguelike-framed result cards shown after a quiz
// outcome is determined (Req 20.2, 20.3, 20.4).
//
// When a node (or probe) quiz is graded, the backend `/decide` endpoint returns
// a banded next-step decision (`DecideResponse`, see lib/game-progress.ts). The
// Play_Surface displays exactly one of three outcome cards corresponding to the
// decided band/action:
//   - CLIMB     → "cleared / advance" (lime)
//   - REINFORCE → "reinforce / hold the line" (cyan)
//   - DESCEND   → "fall-back" framed as roguelike progress, NOT failure (orange)
// The `diagnosis` returned by `decide_next` is shown prominently on whichever
// card is rendered (Req 20.4), and the supporting `gap` / `next_node` fields are
// surfaced where they help frame the next beat.
//
// Per the design's "Medieval pixel-art adventure UX" (Req 22.7, 23): interactive
// chrome stays brutalist — `.brutal-card` / `.brutal` panels, thick ink border,
// flat accent fills, hard shadow — so the card reads as "UI on top of the
// world". Accent mapping follows the Skill_Tree_Map: lime = CLEARED/advance,
// cyan = NEXT/reinforce, orange = fall-back (deliberately NOT pink, which the
// map reserves for the wrong-answer/marker beat, so a descent never reads as a
// failure — Req 20.3).
//
// Purely presentational and prop-driven: the Game_State_Machine owns the
// decision (from the `/api/game/decide` response) and the surrounding flow; this
// component only renders it and reports a "continue" intent back up.

import type { Band, DecideResponse } from "@/lib/game-progress";

// ---------------------------------------------------------------------------
// Per-band presentation (pure config)
// ---------------------------------------------------------------------------
//
// Each band maps to a fixed visual + copy treatment. Copy is roguelike-framed:
// the DESCEND variant is explicitly a "fall-back to build a foundation" beat,
// never a "you failed" beat (Req 20.3).

interface BandPresentation {
  // Flat accent fill for the card (brutalist token).
  accent: string;
  // Pixel-art emblem matching the medieval-adventure read.
  emblem: string;
  // Short eyebrow label (the "result type").
  eyebrow: string;
  // Punchy headline for the outcome.
  title: string;
  // One-line roguelike framing shown under the headline.
  tagline: string;
  // Label for the continue action.
  cta: string;
}

const PRESENTATION: Record<Band, BandPresentation> = {
  CLIMB: {
    accent: "bg-accent-lime",
    emblem: "⚔️",
    eyebrow: "Cleared",
    title: "Node cleared — climb on",
    tagline: "You proved it. The path up the mountain opens.",
    cta: "Advance",
  },
  REINFORCE: {
    accent: "bg-accent-cyan",
    emblem: "🛡️",
    eyebrow: "Reinforce",
    title: "Hold the line — reinforce",
    tagline: "Almost there. One more pass locks this in before you climb.",
    cta: "Reinforce",
  },
  DESCEND: {
    accent: "bg-accent-orange",
    emblem: "🪜",
    eyebrow: "Fall-back",
    title: "Found a gap — fall back to build it",
    tagline:
      "Not a loss — a smarter route. Drop to firmer ground and the climb gets easier.",
    cta: "Descend",
  },
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface OutcomeCardProps {
  // The banded next-step decision returned by `/api/game/decide`. Carries the
  // band/action, the `diagnosis`, and the supporting `gap` / `next_node`.
  decision: DecideResponse;
  // Optional handler for the continue/advance action. When omitted, the CTA is
  // not rendered (e.g. when the parent drives the transition some other way).
  onContinue?: () => void;
  // Optional compact variant for tight surfaces (e.g. over a clip overlay).
  compact?: boolean;
  // Optional extra classes for layout composition by the parent surface.
  className?: string;
}

/**
 * Renders exactly one of the three roguelike outcome cards (CLIMB / REINFORCE /
 * DESCEND) for a graded quiz `decision`, with the `decide_next` diagnosis shown
 * prominently (Req 20.2–20.4). Presentational only.
 */
export default function OutcomeCard({
  decision,
  onContinue,
  compact = false,
  className = "",
}: OutcomeCardProps) {
  // The code-computed band is the source of truth for which card to show; fall
  // back to the LLM `action` only if `band` is somehow absent.
  const band: Band = decision.band ?? decision.action;
  const p = PRESENTATION[band] ?? PRESENTATION.REINFORCE;

  const diagnosis = decision.diagnosis?.trim() ?? "";
  const gap = decision.gap?.trim() ?? "";
  const nextNode = decision.next_node?.trim() ?? "";
  const reachedGoal = band === "CLIMB" && decision.reached_goal;

  return (
    <section
      className={`brutal-card ${p.accent} text-ink ${
        compact ? "px-4 py-3" : "px-5 py-4 sm:px-6 sm:py-5"
      } ${className}`}
      role="status"
      aria-live="polite"
      aria-label={`Outcome: ${p.eyebrow}`}
      data-band={band}
    >
      {/* Eyebrow + emblem: frames the result type with the medieval-adventure read. */}
      <div className="flex items-center gap-2 mb-2">
        <span
          aria-hidden
          className={compact ? "text-lg" : "text-xl"}
          style={{ imageRendering: "pixelated" }}
        >
          {p.emblem}
        </span>
        <span className="text-ink/70 text-[10px] font-black uppercase tracking-widest">
          {p.eyebrow}
        </span>
        {/* When the goal is reached on a CLIMB, flag it as the boss-cleared beat. */}
        {reachedGoal && (
          <span className="brutal bg-accent-yellow text-ink text-[10px] font-black uppercase tracking-wider px-2 py-0.5">
            Goal reached
          </span>
        )}
      </div>

      {/* Headline + roguelike framing. */}
      <h2
        className={`font-black leading-tight ${
          compact ? "text-base" : "text-lg sm:text-xl"
        }`}
      >
        {reachedGoal ? "Boss cleared — you reached the goal" : p.title}
      </h2>
      <p
        className={`font-bold text-ink/80 mt-1 ${
          compact ? "text-xs" : "text-sm"
        }`}
      >
        {p.tagline}
      </p>

      {/* Diagnosis — the `decide_next` explanation, shown prominently (Req 20.4). */}
      <div className="brutal bg-paper text-ink mt-3 px-3 py-2.5">
        <span className="block text-ink/60 text-[10px] font-black uppercase tracking-widest mb-1">
          Diagnosis
        </span>
        {diagnosis ? (
          <p
            className={`font-semibold leading-snug ${
              compact ? "text-sm" : "text-sm sm:text-base"
            }`}
          >
            {diagnosis}
          </p>
        ) : (
          <p className="text-ink/50 text-sm font-medium italic">
            No diagnosis available for this outcome.
          </p>
        )}
      </div>

      {/* Supporting beats: the gap to close and the node we move to next. */}
      {(gap || (nextNode && !reachedGoal)) && (
        <dl className="flex flex-wrap gap-2 mt-3">
          {gap && (
            <div className="brutal bg-white text-ink px-2.5 py-1.5 min-w-0">
              <dt className="text-ink/55 text-[9px] font-black uppercase tracking-widest">
                Gap
              </dt>
              <dd className="text-xs font-bold truncate max-w-[60vw]">{gap}</dd>
            </div>
          )}
          {nextNode && !reachedGoal && (
            <div className="brutal bg-white text-ink px-2.5 py-1.5 min-w-0">
              <dt className="text-ink/55 text-[9px] font-black uppercase tracking-widest">
                {band === "DESCEND" ? "Drop to" : "Next"}
              </dt>
              <dd className="text-xs font-bold truncate max-w-[60vw]">
                {nextNode}
              </dd>
            </div>
          )}
        </dl>
      )}

      {/* Continue action — labeled per band so the verb matches the beat. */}
      {onContinue && (
        <div className="mt-4">
          <button
            type="button"
            onClick={onContinue}
            className="brutal-btn bg-ink text-paper w-full sm:w-auto"
          >
            {reachedGoal ? "Finish" : p.cta}
          </button>
        </div>
      )}
    </section>
  );
}
