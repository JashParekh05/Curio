"use client";

import type { MutableRefObject } from "react";
import { recordClipEvent, incrementGuestProgress, type Clip } from "./api";
import { incrementGuestClips } from "./guest-progress";

export interface LastLogged {
  id: string;
  at: number;
}

interface FlushOpts {
  clip: Clip | undefined;
  startedAt: number;
  sessionId: string | null;
  replayCount: number;
  feedback: "want_more" | "already_know" | null;
  token: string;
  keepalive: boolean;
  isGuest: boolean;
  lastLoggedRef: MutableRefObject<LastLogged | null>;
}

// Records a single clip-watch event, deduped on (clip.id, startedAt) so the same
// dwell is never logged twice. The activeIndex transition (keepalive:false) and
// the unmount / page-hide flush (keepalive:true) share one lastLoggedRef, which
// guarantees the final clip is logged exactly once regardless of which path
// fires first. Used by both the path feed and the discover feed.
export function flushClipEvent(opts: FlushOpts): void {
  const { clip, startedAt, sessionId, replayCount, feedback, token, keepalive, isGuest, lastLoggedRef } = opts;
  if (!clip) return;

  const last = lastLoggedRef.current;
  if (last && last.id === clip.id && last.at === startedAt) return; // already logged this dwell

  const watchMs = Date.now() - startedAt;
  const durationMs = (clip.duration_seconds ?? 60) * 1000;
  recordClipEvent(clip.id, watchMs, watchMs >= durationMs * 0.8, sessionId, replayCount, feedback, token, keepalive);
  lastLoggedRef.current = { id: clip.id, at: startedAt };

  if (isGuest) {
    incrementGuestClips();
    incrementGuestProgress(token); // mirror to server (authoritative across reloads/devices)
  }
}
