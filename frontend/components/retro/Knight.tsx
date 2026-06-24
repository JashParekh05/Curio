"use client";

// Knight — the pixel-art player avatar that travels the WorldMap trail
// (Req 2.1, 2.2, 2.3, 1.4). It is a small, purely presentational sprite that
// WorldMap mounts on the Knight's current Stage and toggles between an idle
// stance and a stepwise walk cycle via the `walking` flag. WorldMap drives that
// flag from the existing descent-walk machinery (`animateDescentTo` /
// `DESCENT_STEP_MS`), so the Knight hops node-to-node on moves with no new
// timing logic of its own (Req 2.2, 2.3).
//
// ── ASSET SUBSTITUTION NOTE ───────────────────────────────────────────────
// The design calls for "idle/walk frames via steps() over a sprite sheet under
// frontend/public/retro/". A binary PNG sprite sheet could not be produced in
// this environment, so by the task's allowed fallback the Knight renders as a
// CSS/emoji pixel sprite that STILL cycles its walk frames in hard steps via
// the shared `.pq-walk` keyframes in `frontend/app/retro.css` (which key off
// `--pq-walk-step-ms`). The component is written so a real sheet is a drop-in:
// pass `spriteSheetUrl` (a 4-frame horizontal sheet) and the same `.pq-walk`
// class drives the `pq-walk-4` background-position cycle instead of the emoji
// bob — no caller change. Until then the emoji layer is the visible Knight.
//
// Reduced motion is handled entirely in CSS: retro.css disables `.pq-walk`
// (and the bob/transition) under `[data-reduced-motion="true"]` and the
// `prefers-reduced-motion: reduce` media query, snapping the sprite to its
// idle/first frame. The Knight therefore renders statically under reduced
// motion without needing to read `matchMedia` itself (Req 1.4, 12.1).
//
// All colors come from the `--pq-*` tokens scoped under `.pixel-quest`
// (retro.css); the sprite layers use `.pixelated` so the art stays crisp at any
// scale (Req 1.2) and stays lightweight CSS, not large media (Req 20.3).

import { type CSSProperties } from "react";

export interface KnightProps {
  // True while the Knight is hopping between Stages along the trail; drives the
  // stepwise walk cycle. False renders the idle stance. Callers pass this purely
  // from the move state — reduced motion is neutralized in CSS, so the Knight is
  // placed statically under reduced motion regardless of this flag (Req 1.4).
  walking?: boolean;
  // Square sprite box size in px (default tuned for a WorldMap tile).
  size?: number;
  // Optional 4-frame horizontal sprite-sheet URL (e.g. "/retro/knight-walk.png").
  // When provided, the sprite renders as a `.pixelated` background-image and the
  // `.pq-walk` class cycles the four frames via the `pq-walk-4` keyframes; when
  // omitted (current state) the emoji fallback is used.
  spriteSheetUrl?: string;
  className?: string;
  // Accessible label override; defaults describe the current motion state.
  label?: string;
}

// Default sprite box, sized to sit comfortably above a WorldMap Stage tile.
const DEFAULT_SIZE = 40;

export default function Knight({
  walking = false,
  size = DEFAULT_SIZE,
  spriteSheetUrl,
  className,
  label,
}: KnightProps) {
  const ariaLabel = label ?? (walking ? "Knight walking" : "Knight is here");

  // Real sprite-sheet path (future asset): a 4-frame horizontal sheet driven by
  // the shared `.pq-walk` cycle. Reduced motion snaps it to frame 0 in CSS.
  if (spriteSheetUrl) {
    const sheetStyle: CSSProperties = {
      width: size,
      height: size,
      backgroundImage: `url(${spriteSheetUrl})`,
      // 4 frames laid out horizontally → the strip is 4× the sprite box wide.
      backgroundSize: "400% 100%",
      backgroundRepeat: "no-repeat",
      backgroundPositionX: 0,
    };
    return (
      <span
        role="img"
        aria-label={ariaLabel}
        className={[
          "pixelated",
          "inline-block",
          walking ? "pq-walk" : "",
          className ?? "",
        ]
          .filter(Boolean)
          .join(" ")}
        style={sheetStyle}
      />
    );
  }

  // Emoji fallback (current): the mounted Knight glyph. When walking it gets the
  // shared `.pq-walk` class so it bobs in hard steps at `--pq-walk-step-ms`
  // (a stepwise walk cycle); idle renders static. `.pixelated` keeps the glyph
  // crisp and consistent with the surrounding pixel art.
  const wrapperStyle: CSSProperties = {
    width: size,
    height: size,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    lineHeight: 1,
    // Sit the sprite just above the tile, like a board-game piece.
    filter: "drop-shadow(2px 2px 0 var(--pq-ink))",
  };

  return (
    <span
      role="img"
      aria-label={ariaLabel}
      className={["pixelated", "inline-flex", className ?? ""]
        .filter(Boolean)
        .join(" ")}
      style={wrapperStyle}
    >
      {/* The glyph layer carries the walk cycle so only it animates (the box
          stays put → no reflow). `.pq-walk` is applied only while walking; CSS
          hard-disables it under reduced motion, leaving the idle frame. */}
      <span
        aria-hidden
        className={walking ? "pq-walk" : ""}
        style={{
          fontSize: Math.round(size * 0.8),
          lineHeight: 1,
          display: "inline-block",
        }}
      >
        🏇
      </span>
    </span>
  );
}
