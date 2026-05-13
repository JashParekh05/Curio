"use client";

import { useRef, useEffect, useState } from "react";
import type { Clip } from "@/lib/api";

interface Props {
  clip: Clip;
  active: boolean;
  onEnded: () => void;
}

export default function ReelPlayer({ clip, active, onEnded }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [muted, setMuted] = useState(false);
  const [showCaption, setShowCaption] = useState(true);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    if (active) {
      video.currentTime = 0;
      video.play().catch(() => {});
    } else {
      video.pause();
    }
  }, [active]);

  return (
    <div className="relative w-full h-full bg-black flex items-center justify-center">
      <video
        ref={videoRef}
        src={clip.video_url}
        className="w-full h-full object-cover"
        playsInline
        muted={muted}
        onEnded={onEnded}
        preload="auto"
      />

      {/* Gradient overlay */}
      <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-transparent to-transparent pointer-events-none" />

      {/* Captions */}
      {showCaption && clip.transcript && (
        <div className="absolute bottom-24 left-4 right-4 text-center">
          <p className="text-white text-sm bg-black/50 rounded-lg px-3 py-2 inline-block">
            {clip.transcript.slice(0, 120)}
            {clip.transcript.length > 120 ? "…" : ""}
          </p>
        </div>
      )}

      {/* Info */}
      <div className="absolute bottom-6 left-4 right-16 space-y-1">
        <p className="text-white font-semibold text-base leading-tight">{clip.title}</p>
        {clip.description && (
          <p className="text-zinc-300 text-sm line-clamp-2">{clip.description}</p>
        )}
        {clip.source_platform && (
          <p className="text-zinc-400 text-xs capitalize">{clip.source_platform.replace("_", " ")}</p>
        )}
      </div>

      {/* Controls */}
      <div className="absolute right-3 bottom-20 flex flex-col gap-4 items-center">
        <button
          onClick={() => setMuted((m) => !m)}
          className="w-10 h-10 rounded-full bg-white/10 backdrop-blur flex items-center justify-center text-white text-lg"
          aria-label="Toggle mute"
        >
          {muted ? "🔇" : "🔊"}
        </button>
        <button
          onClick={() => setShowCaption((c) => !c)}
          className="w-10 h-10 rounded-full bg-white/10 backdrop-blur flex items-center justify-center text-white text-xs font-bold"
          aria-label="Toggle captions"
        >
          CC
        </button>
      </div>
    </div>
  );
}
