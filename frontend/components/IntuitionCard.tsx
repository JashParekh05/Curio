"use client";

// Intuition_Card — the short, punchy second-person mental model delivered for a
// node before its clip and checkpoint (Req 9, 23). The backend `intuition` LLM
// function returns `{hook}` (a 2–3 sentence second-person hook); this component
// renders that `hook` verbatim as a brutalist card.
//
// Per the design's "Medieval pixel-art adventure UX" (Req 22.7, 23): interactive
// chrome stays brutalist — `.brutal-card` panel, thick ink border, flat accent
// fill, hard shadow — so it reads as "UI on top of the world". This same card
// doubles as the caption beat over `ReelPlayer` when a clip plays (Req 23.1), so
// it is kept presentational and prop-driven with no data fetching of its own.

export interface IntuitionCardProps {
  // The second-person hook text produced by the `intuition` LLM function.
  hook: string;
  // Optional node name, shown as a small eyebrow label above the hook so the
  // learner knows which concept the mental model is for.
  node?: string;
  // Optional compact variant for use as the caption beat over a clip overlay,
  // where vertical space is tight (Req 23.1).
  compact?: boolean;
  // Optional extra classes for layout composition by the parent surface.
  className?: string;
}

/**
 * Renders the `hook` as a punchy second-person Intuition_Card using the
 * brutalist design tokens. Purely presentational — the parent owns the `hook`
 * (from the `/api/game/node` response) and the surrounding flow.
 */
export default function IntuitionCard({
  hook,
  node,
  compact = false,
  className = "",
}: IntuitionCardProps) {
  const trimmed = hook?.trim() ?? "";

  return (
    <section
      className={`brutal-card bg-accent-yellow text-ink ${
        compact ? "px-4 py-3" : "px-5 py-4 sm:px-6 sm:py-5"
      } ${className}`}
      aria-label={node ? `Intuition for ${node}` : "Intuition"}
    >
      {/* Eyebrow: frames the card as the "core idea" beat. The pixel-art lightbulb
          keeps the medieval-adventure read while the brutalist chrome frames it. */}
      <div className="flex items-center gap-2 mb-2">
        <span aria-hidden className="text-lg" style={{ imageRendering: "pixelated" }}>
          💡
        </span>
        <span className="text-ink/70 text-[10px] font-black uppercase tracking-widest">
          The Idea
        </span>
        {node && (
          <span className="brutal bg-paper text-ink text-[10px] font-extrabold px-2 py-0.5 truncate max-w-[55%]">
            {node}
          </span>
        )}
      </div>

      {/* The hook itself — rendered verbatim, second-person, punchy. */}
      {trimmed ? (
        <p
          className={`font-extrabold leading-snug ${
            compact ? "text-sm" : "text-base sm:text-lg"
          }`}
        >
          {trimmed}
        </p>
      ) : (
        <p className="text-ink/50 text-sm font-medium italic">
          No intuition available for this node.
        </p>
      )}
    </section>
  );
}
