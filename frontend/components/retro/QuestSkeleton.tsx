"use client";

// QuestSkeleton — the retro loading beat for the Pixel Quest reskin (Req 10.1,
// 10.2). It replaces the plain "Loading the next node…" text with an animated
// "summoning the realm…" shimmer over placeholder map tiles, so the quest never
// shows a blank/frozen screen while:
//
//   - the placement probe is being generated after topic entry (Req 10.1), and
//   - a Node beat is being delivered (Req 10.2).
//
// Per the design (`SkeletonMap` in Components and Interfaces) this is a pure-CSS
// shimmer over placeholder tiles — no media, no sprite sheet, no JS in the hot
// path — so first paint stays fast and the asset stays lightweight (Req 20.3).
// The placeholder trail visually previews the World_Map that is about to load:
// a row of fogged map tiles winding up to the Dragon's Keep at the goal end.
//
// Motion is gated entirely in `retro.css`: the `.pq-shimmer` sweep is
// hard-disabled under `[data-reduced-motion="true"]` (set by RetroThemeProvider)
// and under the native `prefers-reduced-motion: reduce` fallback, so reduced
// motion renders the static placeholder tiles with no sweep (Req 1.4, 12.1). No
// JS is needed for this, but `useRetroSettings().reducedMotion` is available if
// a future variant wants it.
//
// Styling stays in the retro theme layer: `--pq-*` tokens, `.pixel-quest` root,
// `.pixelated` tiles, and the `.pixel-font` chrome face. The region is a labeled
// live `status` so assistive tech announces the loading state (Req 12.3).

export interface QuestSkeletonProps {
  // Optional caption override. Defaults to the "summoning the realm…" beat.
  label?: string;
  // Number of placeholder trail tiles to render before the Keep. Defaults to a
  // short, lightweight trail. Clamped to a sane range.
  tileCount?: number;
  // Optional extra classes for layout from the parent.
  className?: string;
}

// A small, fixed default trail length — enough to read as "a map" without
// rendering a heavy grid.
const DEFAULT_TILES = 8;

/**
 * Renders the retro loading skeleton: a "summoning the realm…" caption above a
 * shimmering trail of placeholder map tiles capped by the Dragon's Keep.
 * Presentational only — the parent decides when it is shown (probe + node
 * delivery windows; task 6.2).
 */
export default function QuestSkeleton({
  label = "Summoning the realm…",
  tileCount = DEFAULT_TILES,
  className,
}: QuestSkeletonProps) {
  // Keep the tile count sane regardless of caller input.
  const count = Math.max(3, Math.min(16, Math.floor(tileCount) || DEFAULT_TILES));
  const tiles = Array.from({ length: count });

  return (
    <section
      className={`pixel-quest relative w-full max-w-2xl mx-auto${
        className ? ` ${className}` : ""
      }`}
      role="status"
      aria-live="polite"
      aria-busy="true"
      aria-label={label}
    >
      <div
        className="px-5 py-6 sm:px-8 sm:py-8"
        style={{
          background: "var(--pq-panel)",
          color: "var(--pq-text)",
          border: "4px solid var(--pq-ink)",
          boxShadow: "6px 6px 0 0 rgba(0, 0, 0, 0.45)",
        }}
      >
        {/* Caption — the "summoning the realm…" beat. The spinner glyph is
            decorative; the visible text carries the meaning. */}
        <div className="flex items-center gap-2 mb-5">
          <span aria-hidden className="pixelated text-base leading-none">
            🗺️
          </span>
          <span
            className="pixel-font uppercase"
            style={{ fontSize: "0.625rem", letterSpacing: "0.12em" }}
          >
            {label}
          </span>
        </div>

        {/* Placeholder trail — a relatively-positioned tile row with a single
            absolutely-positioned shimmer sweep layered over it. The sweep is
            `pointer-events: none` and purely cosmetic; it is disabled under
            reduced motion (retro.css), leaving the static tiles. */}
        <div className="relative overflow-hidden" aria-hidden>
          <div className="flex flex-wrap items-end gap-2 sm:gap-3">
            {tiles.map((_, i) => (
              <div
                key={i}
                className="pixelated"
                style={{
                  // Slight size variation so the trail reads as map tiles, not
                  // a flat progress bar. Cheap, deterministic per-index.
                  width: i % 3 === 0 ? "2.75rem" : "2.25rem",
                  height: i % 3 === 0 ? "2.25rem" : "1.75rem",
                  background:
                    i % 2 === 0 ? "var(--pq-locked)" : "var(--pq-bg)",
                  border: "3px solid var(--pq-ink)",
                  boxShadow: "2px 2px 0 0 rgba(0, 0, 0, 0.35)",
                }}
              />
            ))}

            {/* The Dragon's Keep caps the goal end of the trail (mirrors the
                WorldMap goal tile). Larger + tinted so the destination reads. */}
            <div
              className="pixelated flex items-center justify-center"
              style={{
                width: "3.25rem",
                height: "3rem",
                background: "var(--pq-keep, var(--pq-biome-keep))",
                border: "3px solid var(--pq-ink)",
                boxShadow: "2px 2px 0 0 rgba(0, 0, 0, 0.45)",
              }}
            >
              <span className="pixelated text-lg leading-none" aria-hidden>
                🏰
              </span>
            </div>
          </div>

          {/* The shimmer sweep — a translucent highlight band that steps across
              the tiles. `.pq-shimmer` carries the gradient + animation; reduced
              motion disables the sweep (retro.css). */}
          <div
            className="pq-shimmer absolute inset-0"
            style={{ pointerEvents: "none" }}
          />
        </div>
      </div>
    </section>
  );
}
