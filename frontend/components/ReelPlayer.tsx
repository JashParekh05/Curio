"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { Clip } from "@/lib/api";
import type { OverlayMetadata } from "@/lib/overlay-cache";

interface Props {
  clip: Clip;
  mode: "active" | "warm";
  onEnded: () => void;
  onFeedback?: (type: "want_more" | "already_know") => void;
  overlay?: OverlayMetadata;
}

function isYouTubeEmbed(url: string) {
  return url.includes("youtube.com/embed");
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

export default function ReelPlayer({ clip, mode, onEnded, onFeedback, overlay }: Props) {
  const active = mode === "active";
  const videoRef = useRef<HTMLVideoElement>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [videoError, setVideoError] = useState(false);
  const [feedback, setFeedback] = useState<"want_more" | "already_know" | null>(null);

  const isYT = isYouTubeEmbed(clip.video_url);

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
  // preload="auto" keeps warm clips buffering silently.
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
          className="absolute inset-0 w-full h-full"
          allow="autoplay; encrypted-media; fullscreen"
          allowFullScreen
        />
      ) : (
        <video
          ref={videoRef}
          src={clip.video_url}
          className="absolute inset-0 w-full h-full object-cover"
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
          <p className="brutal-dark bg-ink text-white text-sm font-bold px-3 py-2">Couldn&apos;t load video</p>
          <button
            onClick={onEnded}
            className="brutal-dark-btn bg-accent-yellow text-ink text-sm font-bold px-4 py-2"
          >
            Skip
          </button>
        </div>
      )}

      {/* Caption bar */}
      <div className="absolute bottom-28 inset-x-0 z-10 pl-4 pr-20 pb-2 pointer-events-none">
        <span className="brutal-dark inline-block bg-ink text-white font-extrabold text-base leading-snug px-2 py-1 line-clamp-2">{overlay?.title ?? clip.title}</span>
        {(overlay?.description ?? clip.description) && (
          <p className="text-white text-sm mt-2 leading-snug drop-shadow line-clamp-2 font-medium">{overlay?.description ?? clip.description}</p>
        )}
      </div>

      {/* Feedback buttons — vertically centered on the right edge (Reels-style) so
          they clear the player's bottom control bar on mobile (the scrubber,
          fullscreen, and the settings gear that holds playback speed / 2x). */}
      {active && onFeedback && (
        <div className="absolute right-3 top-1/2 -translate-y-1/2 flex flex-col gap-3 items-center z-10">
          <button
            onClick={() => { setFeedback("want_more"); onFeedback("want_more"); }}
            disabled={feedback !== null}
            aria-label="I want more of this"
            className={`brutal-dark-btn w-12 h-12 flex items-center justify-center text-[10px] font-extrabold uppercase tracking-tight disabled:cursor-default ${
              feedback === "want_more"
                ? "bg-accent-orange text-ink"
                : "bg-ink text-white"
            }`}
            title="I want more of this"
          >
            More
          </button>
          <button
            onClick={() => { setFeedback("already_know"); onFeedback("already_know"); }}
            disabled={feedback !== null}
            aria-label="I already know this topic"
            className={`brutal-dark-btn w-12 h-12 flex items-center justify-center text-[10px] font-extrabold uppercase tracking-tight disabled:cursor-default ${
              feedback === "already_know"
                ? "bg-accent-lime text-ink"
                : "bg-ink text-white"
            }`}
            title="I already know this topic"
          >
            Know
          </button>
        </div>
      )}
    </div>
  );
}
