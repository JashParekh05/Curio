"use client";

// ClipCutscene — the retro pixel-art reskin of `ClipOverlay` (Req 6.2, 6.3,
// 8.4, 11.1, 11.3).
//
// Where `ClipOverlay` framed the clip beat as a plain TikTok-style vertical
// player, `ClipCutscene` frames the SAME reused `ReelPlayer` inside a pixel
// "vision/cutscene" border — the Knight peering into a scrying vision of the
// concept — with the node hook rendered as the caption beat via the
// `IntuitionScroll` (compact variant) and an always-present soft "Continue"
// (Req 6.2, 6.4).
//
// This is a pure reskin that preserves the EXACT prop contract of `ClipOverlay`
// (`clip, hook?, node?, mode?, onEnded, onFeedback?, onContinue?`) so the
// Game_State_Machine wiring in `play/page.tsx` only swaps the import (Req 11.2),
// and it REUSES rather than forks both `ReelPlayer` and the `nodeClipToClip`
// mapping (Req 8.4, 11.3).
//
// Clip-absent contract: the page skips straight to the checkpoint when a node
// has no clip (Req 6.3), so this surface normally only mounts with a real clip.
// As a belt-and-braces guard it still never renders a broken player — when the
// clip (or its `video_url`) is missing it shows the scroll + Continue alone, so
// the beat never hard-blocks the learner (Req 6.3, 6.4).
//
// All retro colors come from the `--pq-*` tokens scoped under `.pixel-quest`
// (see `frontend/app/retro.css`); the cutscene border glow is gated by
// `useRetroSettings().reducedMotion` so motion-sensitive learners get a static
// frame (Req 1.4, 12.1).

import { nodeClipToClip, type ClipOverlayProps } from "@/components/ClipOverlay";
import ReelPlayer from "@/components/ReelPlayer";

import IntuitionScroll from "./IntuitionScroll";
import { useRetroSettings } from "./RetroThemeProvider";

// Preserve the exact `ClipOverlay` prop contract so the wiring is unchanged
// (Req 11.2). Re-exported under a cutscene-specific name for clarity.
export type ClipCutsceneProps = ClipOverlayProps;

/**
 * Renders the clip beat as a retro cutscene: the reused `ReelPlayer` inside a
 * pixel "vision" frame, the node hook as an `IntuitionScroll` (compact) caption,
 * and an always-available soft "Continue" affordance. When no playable clip is
 * present it degrades to the scroll + Continue alone — never a broken player.
 */
export default function ClipCutscene({
  clip,
  hook,
  node,
  mode = "active",
  onEnded,
  onFeedback,
  onContinue,
}: ClipCutsceneProps) {
  const { reducedMotion } = useRetroSettings();

  const trimmedHook = hook?.trim() ?? "";
  // Soft continue: always advances the flow, falling back to `onEnded` when no
  // explicit continue handler is supplied (mirrors `ClipOverlay`, Req 6.4).
  const advance = onContinue ?? onEnded;
  // Only mount the reused `ReelPlayer` when there is something playable — guard
  // against a missing clip or an empty `video_url` so we never render a broken
  // player (Req 6.3).
  const hasClip = Boolean(clip && clip.video_url && clip.video_url.trim());

  // The cutscene border carries a soft vision glow; disable the glow halo under
  // reduced motion so the frame is fully static (Req 1.4, 12.1).
  const frameShadow = reducedMotion
    ? "4px 4px 0 0 var(--pq-ink)"
    : "4px 4px 0 0 var(--pq-ink), 0 0 18px 3px var(--pq-cyan)";

  return (
    <div
      className="pixelated relative w-full h-full overflow-hidden"
      style={{ background: "var(--pq-bg)", color: "var(--pq-text)" }}
      aria-label={node ? `Vision of ${node}` : "Clip cutscene"}
    >
      {/* The pixel "vision/cutscene" frame: an inset bordered window the clip
          plays inside, selling the Knight's scrying-vision read (Req 6.2). */}
      <div
        className="pixelated absolute inset-0 flex flex-col"
        style={{
          margin: "0.75rem",
          border: "4px solid var(--pq-ink)",
          boxShadow: frameShadow,
          background: "#000",
        }}
      >
        {/* Cutscene chrome bar — labels the beat as a vision in the pixel face. */}
        <div
          className="pixel-font"
          style={{
            display: "flex",
            alignItems: "center",
            gap: "0.4rem",
            background: "var(--pq-ink)",
            color: "var(--pq-cyan)",
            padding: "0.3rem 0.55rem",
            fontSize: "0.5rem",
            textTransform: "uppercase",
            letterSpacing: "0.12em",
            flex: "0 0 auto",
          }}
        >
          <span aria-hidden className="pixelated" style={{ lineHeight: 1 }}>
            🔮
          </span>
          <span>Vision</span>
        </div>

        {/* The vision window itself — the reused ReelPlayer, or a static
            fallback panel when no clip is playable (Req 6.3, 8.4). */}
        <div className="relative" style={{ flex: "1 1 auto", minHeight: 0 }}>
          {hasClip ? (
            <ReelPlayer
              clip={nodeClipToClip(clip)}
              mode={mode}
              onEnded={onEnded}
              onFeedback={onFeedback}
            />
          ) : (
            <div
              className="pixel-body absolute inset-0 flex items-center justify-center text-center"
              style={{
                background: "var(--pq-bg)",
                color: "var(--pq-text)",
                opacity: 0.75,
                padding: "1.5rem",
              }}
            >
              The vision is clouded — read the scroll and press on.
            </div>
          )}

          {/* Hook as the caption beat — the compact IntuitionScroll riding over
              the vision, kept clear of ReelPlayer's own caption/feedback rails.
              `pointer-events-none` lets clip taps pass through (Req 6.2). */}
          {trimmedHook && (
            <div className="absolute top-2 inset-x-0 z-20 px-2 pr-14 pointer-events-none">
              <IntuitionScroll hook={trimmedHook} node={node} compact />
            </div>
          )}
        </div>
      </div>

      {/* Soft continue affordance — always present so the cutscene never
          hard-blocks the flow (Req 6.4), independent of clip playback. */}
      <div className="absolute bottom-5 inset-x-0 z-30 flex justify-center px-4 pointer-events-none">
        <button
          type="button"
          onClick={advance}
          className="pixel-font pointer-events-auto"
          style={{
            background: "var(--pq-gold)",
            color: "var(--pq-ink)",
            border: "3px solid var(--pq-ink)",
            boxShadow: "3px 3px 0 0 var(--pq-ink)",
            padding: "0.55rem 1rem",
            fontSize: "0.6rem",
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            cursor: "pointer",
          }}
        >
          Continue
        </button>
      </div>
    </div>
  );
}
