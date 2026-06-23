"use client";

// ClipOverlay — the TikTok-style clip beat of the Play_Surface (Req 22.4, 23.1,
// 23.4). When a node is delivered with a selected clip, this surface plays that
// clip in a short-form vertical (TikTok-style) frame by REUSING the feed's
// `ReelPlayer` verbatim, and overlays the node's Intuition_Card `hook` as the
// caption beat.
//
// Design notes:
// - `ReelPlayer` is reused with its real props — `clip`, `mode`, `onEnded`,
//   `onFeedback` — and expects a `lib/api` `Clip` whose `video_url` is the
//   `youtube.com/embed/...` form (Req 10.3). The game speaks in `NodeClip`
//   (the `/api/game/node` wire shape), so `nodeClipToClip` maps one to the
//   other; no other behavior of `ReelPlayer` is touched.
// - The Intuition_Card hook rides on top as a compact caption beat (Req 23.1),
//   reusing the existing `IntuitionCard` component in its `compact` variant.
// - Beats stay short and the checkpoint stays SOFT: a "Continue" affordance is
//   always present so the learner can move on at any time and is never
//   hard-blocked (Req 23.4), independent of whether the clip has ended.

import type { Clip } from "@/lib/api";
import type { NodeClip } from "@/lib/game-progress";
import ReelPlayer from "@/components/ReelPlayer";
import IntuitionCard from "@/components/IntuitionCard";

export interface ClipOverlayProps {
  // The selected clip for this node (from the `/api/game/node` response). Its
  // `video_url` is already the canonical `youtube.com/embed/...` form (Req 10.3).
  clip: NodeClip;
  // The Intuition_Card hook for this node, shown as the caption beat over the
  // clip (Req 23.1). Optional — when absent, the clip plays without the beat.
  hook?: string;
  // The node name, used as the Intuition_Card eyebrow label.
  node?: string;
  // Playback mode forwarded to `ReelPlayer`. Defaults to "active".
  mode?: "active" | "warm";
  // Called when the clip finishes (or is skipped from `ReelPlayer`'s error UI).
  onEnded: () => void;
  // Optional "want more" / "already know" feedback, forwarded to `ReelPlayer`.
  onFeedback?: (type: "want_more" | "already_know") => void;
  // Soft continue affordance (Req 23.4). When provided, a brutalist "Continue"
  // button is rendered so the learner can advance at any time and is never
  // hard-blocked. Falls back to `onEnded` when omitted.
  onContinue?: () => void;
}

/**
 * Maps the game's `NodeClip` wire shape onto the `lib/api` `Clip` shape that
 * `ReelPlayer` consumes. `video_url` is passed through unchanged because it is
 * already the `youtube.com/embed/...` form `ReelPlayer`'s embed detection
 * expects (Req 10.3). Fields `ReelPlayer` does not read are filled with inert
 * defaults so the reuse stays behavior-preserving.
 */
export function nodeClipToClip(clip: NodeClip): Clip {
  return {
    id: clip.video_id,
    topic_slug: "",
    title: clip.title,
    description: clip.description,
    video_url: clip.video_url,
    thumbnail_url: clip.thumbnail_url,
    duration_seconds: clip.duration_seconds,
    transcript: null,
    source_url: null,
    source_platform: "youtube",
    hook_score: 0,
    final_score: null,
    section_index: null,
  };
}

/**
 * Renders the short-form vertical clip beat: a reused `ReelPlayer` filling a
 * TikTok-style frame with the Intuition_Card hook as a compact caption beat and
 * an always-available soft "Continue" affordance.
 */
export default function ClipOverlay({
  clip,
  hook,
  node,
  mode = "active",
  onEnded,
  onFeedback,
  onContinue,
}: ClipOverlayProps) {
  const trimmedHook = hook?.trim() ?? "";
  const advance = onContinue ?? onEnded;

  return (
    <div className="relative w-full h-full bg-black overflow-hidden">
      {/* Reused feed player — short-form vertical clip playback (Req 22.4). */}
      <ReelPlayer
        clip={nodeClipToClip(clip)}
        mode={mode}
        onEnded={onEnded}
        onFeedback={onFeedback}
      />

      {/* Intuition_Card hook as the caption beat, kept short and out of the way
          of ReelPlayer's own caption bar (bottom) and feedback rail (right).
          `pointer-events-none` on the wrapper lets clip taps pass through; the
          card itself is non-interactive so this is safe. */}
      {trimmedHook && (
        <div className="absolute top-3 inset-x-0 z-20 px-3 pr-16 pointer-events-none">
          <IntuitionCard hook={trimmedHook} node={node} compact />
        </div>
      )}

      {/* Soft continue affordance — always present so the beat never hard-blocks
          the flow (Req 23.4). */}
      <div className="absolute bottom-4 inset-x-0 z-20 flex justify-center px-4 pointer-events-none">
        <button
          type="button"
          onClick={advance}
          className="brutal-dark-btn pointer-events-auto bg-accent-yellow text-ink text-sm font-extrabold uppercase tracking-tight px-5 py-2"
        >
          Continue
        </button>
      </div>
    </div>
  );
}
