"use client";

// IntuitionScroll — the retro pixel-art reskin of `IntuitionCard` (Req 6.1,
// 11.1, 11.2).
//
// Where `IntuitionCard` drew the node hook as a brutalist yellow card, the
// IntuitionScroll renders the SAME `hook` on a parchment scroll using the
// `--pq-*` retro tokens scoped under `.pixel-quest` (see
// `frontend/app/retro.css`). It is a pure reskin — it preserves the EXACT props
// and data contract of `IntuitionCard` (`hook`, `node?`, `compact?`,
// `className?`) so the Game_State_Machine wiring in `play/page.tsx` only swaps
// the import (Req 11.2).
//
// The `compact` variant is the caption beat used inside the `ClipCutscene`,
// where vertical space is tight: it drops to a smaller parchment footprint and
// tighter type while still reading as the same scroll (Req 6.1, design
// "the `compact` variant is the caption beat inside `ClipCutscene`").
//
// Purely presentational and prop-driven (no data fetching): the parent owns the
// `hook` (from the `/api/game/node` response) and the surrounding flow.

export interface IntuitionScrollProps {
  // The second-person hook text produced by the `intuition` LLM function.
  hook: string;
  // Optional node name, shown as a small eyebrow label above the hook so the
  // learner knows which concept the mental model is for.
  node?: string;
  // Optional compact variant for use as the caption beat over the clip
  // cutscene, where vertical space is tight (Req 6.1).
  compact?: boolean;
  // Optional extra classes for layout composition by the parent surface.
  className?: string;
}

/**
 * Renders the `hook` on a parchment scroll using the retro `--pq-*` tokens.
 * Presentational only — the parent owns the `hook` and the surrounding flow.
 * Reskins `IntuitionCard` with identical props so the page wiring is unchanged.
 */
export default function IntuitionScroll({
  hook,
  node,
  compact = false,
  className = "",
}: IntuitionScrollProps) {
  const trimmed = hook?.trim() ?? "";

  return (
    <section
      className={`pixelated relative ${className}`}
      aria-label={node ? `Intuition for ${node}` : "Intuition"}
      // Stable test/styling hook so the caption-beat (compact) variant used
      // inside the `ClipCutscene` is identifiable without inspecting styles.
      data-compact={compact ? "true" : "false"}
      style={{
        // Parchment scroll surface: the `--pq-paper` token on an inked frame
        // with a hard pixel shadow so it reads as a physical scroll the Knight
        // unfurls and reads.
        background: "var(--pq-paper)",
        color: "var(--pq-ink)",
        border: "4px solid var(--pq-ink)",
        boxShadow: "4px 4px 0 0 var(--pq-ink)",
        padding: compact ? "0.6rem 0.75rem" : "0.9rem 1.1rem",
      }}
    >
      {/* Eyebrow: frames the scroll as the "core idea" beat. The pixel-art
          scroll glyph keeps the medieval-adventure read; the node chip tells the
          learner which concept this mental model is for. */}
      <div
        className="flex items-center"
        style={{ gap: "0.5rem", marginBottom: compact ? "0.4rem" : "0.55rem" }}
      >
        <span aria-hidden className="pixelated" style={{ fontSize: "1rem" }}>
          📜
        </span>
        <span
          className="pixel-font"
          style={{
            fontSize: "0.4rem",
            textTransform: "uppercase",
            letterSpacing: "0.14em",
            opacity: 0.75,
          }}
        >
          The Idea
        </span>
        {node && (
          <span
            className="pixel-font pixelated truncate"
            style={{
              background: "var(--pq-gold)",
              color: "var(--pq-ink)",
              border: "2px solid var(--pq-ink)",
              padding: "0.15rem 0.35rem",
              fontSize: "0.4rem",
              maxWidth: "55%",
            }}
          >
            {node}
          </span>
        )}
      </div>

      {/* The hook itself — rendered verbatim, second-person, punchy, in the
          legible body face so the parchment styling never costs readability
          (Req 1.5). */}
      {trimmed ? (
        <p
          className="pixel-body"
          style={{
            color: "var(--pq-ink)",
            fontWeight: 700,
            lineHeight: 1.5,
            margin: 0,
            fontSize: compact ? "0.95rem" : undefined,
          }}
        >
          {trimmed}
        </p>
      ) : (
        <p
          className="pixel-body"
          style={{
            color: "var(--pq-ink)",
            fontStyle: "italic",
            opacity: 0.55,
            margin: 0,
            fontSize: compact ? "0.9rem" : undefined,
          }}
        >
          No intuition available for this node.
        </p>
      )}
    </section>
  );
}
