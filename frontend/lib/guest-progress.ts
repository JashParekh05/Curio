"use client";

// Lightweight localStorage tracking for the soft signup gate. Kept out of React
// state so the counter survives reloads and is shared across the separate
// /feed and /discover route components. Increments dispatch a window event so a
// mounted GuestGate can react without polling.

const CLIPS_KEY = "curio_guest_clips";
const DISMISSED_KEY = "curio_guest_gate_dismissed";

export const GUEST_GATE_THRESHOLD = 5;
export const GUEST_CLIP_EVENT = "curio:guest-clip";

export function getGuestClips(): number {
  if (typeof window === "undefined") return 0;
  return parseInt(localStorage.getItem(CLIPS_KEY) ?? "0", 10) || 0;
}

export function incrementGuestClips(): number {
  if (typeof window === "undefined") return 0;
  const next = getGuestClips() + 1;
  try {
    localStorage.setItem(CLIPS_KEY, String(next));
    window.dispatchEvent(new CustomEvent(GUEST_CLIP_EVENT, { detail: next }));
  } catch {
    /* storage unavailable (private mode quota etc.) — gate just won't fire */
  }
  return next;
}

export function isGateDismissed(): boolean {
  if (typeof window === "undefined") return false;
  return localStorage.getItem(DISMISSED_KEY) === "1";
}

export function dismissGate(): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(DISMISSED_KEY, "1");
  } catch {
    /* ignore */
  }
}

// Called after a guest upgrades to a real account so a future guest session
// (e.g. after sign out) starts clean.
export function resetGuestProgress(): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.removeItem(CLIPS_KEY);
    localStorage.removeItem(DISMISSED_KEY);
  } catch {
    /* ignore */
  }
}
