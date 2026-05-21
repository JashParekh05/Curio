"use client";

import { useEffect, useRef, useState } from "react";
import type { Clip } from "@/lib/api";

interface Props {
  clip: Clip;
  active: boolean;
  onEnded: () => void;
  onFeedback?: (type: "want_more" | "already_know") => void;
}

function isYouTubeEmbed(url: string) {
  return url.includes("youtube.com/embed");
}

function sanitizeYTUrl(url: string): string {
  try {
    const u = new URL(url);
    u.searchParams.set("enablejsapi", "1");
    u.searchParams.set("autoplay", "1");
    u.searchParams.set("rel", "0");
    u.searchParams.set("modestbranding", "1");
    u.searchParams.set("origin", window.location.origin);
    return u.toString();
  } catch {
    return url;
  }
}

export default function ReelPlayer({ clip, active, onEnded, onFeedback }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [videoError, setVideoError] = useState(false);
  const [feedback, setFeedback] = useState<"want_more" | "already_know" | null>(null);

  const isYT = isYouTubeEmbed(clip.video_url);

  useEffect(() => {
    setVideoError(false);
    setFeedback(null);
  }, [clip.id]);

  // Native video: play/reset on active
  useEffect(() => {
    if (isYT || !videoRef.current) return;
    if (active) {
      videoRef.current.currentTime = 0;
      videoRef.current.play().catch(() => {});
    } else {
      videoRef.current.pause();
    }
  }, [active, isYT]);

  // Pause/play inactive YouTube iframes via postMessage
  useEffect(() => {
    if (!isYT) return;
    iframeRef.current?.contentWindow?.postMessage(
      JSON.stringify({ event: "command", func: active ? "playVideo" : "pauseVideo", args: [] }),
      "*"
    );
  }, [active, isYT]);

  return (
    <div className="absolute inset-0 bg-black">
      {isYT ? (
        <iframe
          ref={iframeRef}
          key={clip.id}
          src={sanitizeYTUrl(clip.video_url)}
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
          onEnded={onEnded}
          onError={() => setVideoError(true)}
          preload="auto"
        />
      )}

      {/* Native video load error */}
      {videoError && (
        <div className="absolute inset-0 bg-black/80 flex flex-col items-center justify-center gap-3 z-10">
          <p className="text-zinc-400 text-sm">Couldn&apos;t load video</p>
          <button
            onClick={onEnded}
            className="bg-white/10 text-white text-sm px-4 py-2 rounded-xl hover:bg-white/20 transition"
          >
            Skip →
          </button>
        </div>
      )}

      {/* Caption bar */}
      <div className="absolute bottom-28 inset-x-0 z-10 pl-4 pr-16 pb-2 pointer-events-none">
        <p className="text-white font-semibold text-base leading-snug drop-shadow-lg line-clamp-2">{clip.title}</p>
        {clip.description && (
          <p className="text-zinc-300 text-sm mt-1 leading-snug drop-shadow line-clamp-2">{clip.description}</p>
        )}
      </div>

      {/* Feedback buttons — always visible */}
      {onFeedback && (
        <div className="absolute right-3 bottom-16 flex flex-col gap-3 items-center z-10">
          <button
            onClick={() => { setFeedback("want_more"); onFeedback("want_more"); }}
            disabled={feedback !== null}
            className={`w-11 h-11 rounded-full backdrop-blur-sm border flex items-center justify-center text-lg transition active:scale-95 disabled:cursor-default ${
              feedback === "want_more"
                ? "bg-orange-500/80 border-orange-400 text-white"
                : "bg-black/30 border-zinc-700 text-zinc-400 hover:border-orange-500/60 hover:text-orange-400"
            }`}
            title="I want more of this"
          >
            🔥
          </button>
          <button
            onClick={() => { setFeedback("already_know"); onFeedback("already_know"); }}
            disabled={feedback !== null}
            className={`w-11 h-11 rounded-full backdrop-blur-sm border flex items-center justify-center text-lg transition active:scale-95 disabled:cursor-default ${
              feedback === "already_know"
                ? "bg-emerald-500/80 border-emerald-400 text-white"
                : "bg-black/30 border-zinc-700 text-zinc-400 hover:border-emerald-500/60 hover:text-emerald-400"
            }`}
            title="I already know this topic"
          >
            ✓
          </button>
        </div>
      )}
    </div>
  );
}
