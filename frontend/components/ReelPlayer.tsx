"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { Clip } from "@/lib/api";
import type { OverlayMetadata } from "@/lib/overlay-cache";

interface Props {
  clip: Clip;
  mode: "active" | "warm";
  onEnded: () => void;
  onFeedback?: (type: "want_more" | "already_know") => void;
  // Optional cross-link: when provided (Discover only), shows a "Learn" action
  // that takes the learner deeper into this clip's topic.
  onLearnThis?: () => void;
  overlay?: OverlayMetadata;
}

function isYouTubeEmbed(url: string) {
  return url.includes("youtube.com/embed") || url.includes("youtube-nocookie.com/embed");
}

// Build a watch-on-YouTube URL (with start time) so every embedded clip links
// back to YouTube — attribution required under YouTube's embed terms. Prefers
// the stored source URL; derives a watch URL from the embed; and ALWAYS returns
// a link (generic youtube.com) so attribution can never silently disappear.
function youtubeWatchUrl(clip: Clip): string {
  if (clip.source_url) return clip.source_url;
  try {
    const u = new URL(clip.video_url);
    const m = u.pathname.match(/\/embed\/([^/?]+)/);
    if (m) {
      const start = u.searchParams.get("start");
      return `https://www.youtube.com/watch?v=${m[1]}${start ? `&t=${start}s` : ""}`;
    }
  } catch {
    /* fall through to the generic link below */
  }
  return "https://www.youtube.com";
}

function sanitizeYTUrl(url: string, active: boolean): string {
  try {
    const u = new URL(url);
    u.searchParams.set("enablejsapi", "1");
    u.searchParams.set("autoplay", active ? "1" : "0");
    u.searchParams.set("mute", active ? "0" : "1");
    u.searchParams.set("rel", "0");
    u.searchParams.set("modestbranding", "1");
    u.searchParams.set("origin", window.location.origin);
    return u.toString();
  } catch {
    return url;
  }
}

export default function ReelPlayer({ clip, mode, onEnded, onFeedback, onLearnThis, overlay }: Props) {
  const active = mode === "active";
  const videoRef = useRef<HTMLVideoElement>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [videoError, setVideoError] = useState(false);
  const [feedback, setFeedback] = useState<"want_more" | "already_know" | null>(null);

  const isYT = isYouTubeEmbed(clip.video_url);
  const watchUrl = isYT ? youtubeWatchUrl(clip) : null;

  // Build the iframe src once per clip so toggling active/warm never remounts or
  // reloads the embed (key stays clip.id); play/mute transitions go through the
  // postMessage effect below instead.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const ytSrc = useMemo(() => (isYT ? sanitizeYTUrl(clip.video_url, active) : ""), [clip.id, isYT]);

  useEffect(() => {
    setVideoError(false);
    setFeedback(null);
  }, [clip.id]);

  // Native video: mute when warm, play from start on active, pause otherwise.
  useEffect(() => {
    const v = videoRef.current;
    if (isYT || !v) return;
    v.muted = !active;
    if (active) {
      v.currentTime = 0;
      v.play().catch(() => {});
    } else {
      v.pause();
    }
  }, [active, isYT]);

  // YouTube iframe: active -> unMute + playVideo; warm -> pauseVideo + mute.
  useEffect(() => {
    if (!isYT) return;
    const win = iframeRef.current?.contentWindow;
    const cmd = (func: string) =>
      JSON.stringify({ event: "command", func, args: [] });
    if (active) {
      win?.postMessage(cmd("unMute"), "*");
      win?.postMessage(cmd("playVideo"), "*");
    } else {
      win?.postMessage(cmd("pauseVideo"), "*");
      win?.postMessage(cmd("mute"), "*");
    }
  }, [active, isYT]);

  return (
    <div
      className="absolute inset-0 bg-black"
      aria-hidden={!active}
      tabIndex={active ? undefined : -1}
      inert={!active}
    >
      {isYT ? (
        <iframe
          ref={iframeRef}
          key={clip.id}
          src={ytSrc}
          title={clip.title}
          className="absolute inset-x-0 top-0 w-full h-[calc(100%-64px-env(safe-area-inset-bottom))]"
          allow="autoplay; encrypted-media; fullscreen"
          allowFullScreen
        />
      ) : (
        <video
          ref={videoRef}
          src={clip.video_url}
          className="absolute inset-x-0 top-0 w-full h-[calc(100%-64px-env(safe-area-inset-bottom))] object-cover"
          playsInline
          muted={!active}
          onEnded={onEnded}
          onError={() => setVideoError(true)}
          preload="auto"
        />
      )}

      {/* Native video load error */}
      {videoError && (
        <div className="absolute inset-0 bg-black/80 flex flex-col items-center justify-center gap-4 z-10">
          <p className="text-white text-sm font-semibold">Couldn&apos;t load video</p>
          <button
            onClick={onEnded}
            className="rounded-pill bg-primary text-on-primary text-sm font-semibold px-5 py-2.5 shadow-elev-1 transition hover:brightness-[1.05]"
          >
            Skip
          </button>
        </div>
      )}

      {/* Caption — clean white text with a strong shadow for legibility over any
          video (no hard box, no scrim covering the player's own controls). */}
      <div className="absolute bottom-28 inset-x-0 z-10 pl-4 pr-20 pb-2 pointer-events-none">
        <h2 className="text-white font-extrabold text-lg leading-snug line-clamp-2 drop-shadow-[0_2px_4px_rgba(0,0,0,0.85)]">
          {overlay?.title ?? clip.title}
        </h2>
        {(overlay?.description ?? clip.description) && (
          <p className="text-white/90 text-sm mt-1.5 leading-snug line-clamp-2 font-medium drop-shadow-[0_1px_3px_rgba(0,0,0,0.85)]">
            {overlay?.description ?? clip.description}
          </p>
        )}
        {isYT && watchUrl && (
          <a
            href={watchUrl}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="pointer-events-auto inline-flex items-center gap-1 mt-2 rounded-pill bg-black/55 backdrop-blur-sm text-white text-xs font-semibold px-3 py-1.5 transition hover:bg-black/70"
          >
            Watch on YouTube ↗
          </a>
        )}
      </div>

      {/* Right-edge control stack (Reels-style), vertically centered so it clears
          the player's bottom control bar on mobile. Clean glassy pills; selected
          feedback states fill with a token color. Text labels — no emoji. */}
      {active && (onFeedback || onLearnThis) && (
        <div className="absolute right-3 top-1/2 -translate-y-1/2 flex flex-col gap-3 items-center z-10">
          {onLearnThis && (
            <button
              onClick={onLearnThis}
              aria-label="Learn this topic"
              title="Learn this topic"
              className="w-12 h-12 rounded-full bg-primary text-on-primary flex items-center justify-center text-[10px] font-bold uppercase tracking-tight shadow-elev-2 transition hover:brightness-[1.05]"
            >
              Learn
            </button>
          )}
          {onFeedback && (
            <>
              <button
                onClick={() => { setFeedback("want_more"); onFeedback("want_more"); }}
                disabled={feedback !== null}
                aria-label="I want more of this"
                title="I want more of this"
                className={`w-12 h-12 rounded-full backdrop-blur-sm flex items-center justify-center text-[10px] font-bold uppercase tracking-tight transition disabled:cursor-default ${
                  feedback === "want_more" ? "bg-success text-white" : "bg-black/40 text-white hover:bg-black/55"
                }`}
              >
                More
              </button>
              <button
                onClick={() => { setFeedback("already_know"); onFeedback("already_know"); }}
                disabled={feedback !== null}
                aria-label="I already know this topic"
                title="I already know this topic"
                className={`w-12 h-12 rounded-full backdrop-blur-sm flex items-center justify-center text-[10px] font-bold uppercase tracking-tight transition disabled:cursor-default ${
                  feedback === "already_know" ? "bg-surface text-on-surface" : "bg-black/40 text-white hover:bg-black/55"
                }`}
              >
                Know
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
