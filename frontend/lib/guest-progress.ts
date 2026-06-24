"use client";

// Lightweight localStorage tracking for the soft signup gate. Kept out of React
// state so the counter survives reloads and is shared across the separate
// /feed and /discover route components. Increments dispatch a window event so a
// mounted GuestGate can react without polling.

const CLIPS_KEY = "curio_guest_clips";
const DISMISSED_KEY = "curio_guest_gate_dismissed";

export const GUEST_GATE_THRESHOLD = 5;
// Hard wall: once a guest has watched this many clips, watching is blocked until
// they create an account. The soft banner (GUEST_GATE_THRESHOLD) is a dismissible
// nudge; this limit is not dismissible. Tune to taste — a single number.
export const GUEST_HARD_LIMIT = 7;
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

// Reconcile the local counter UP to the server-authoritative count (keyed on the
// anonymous user_id) so clearing localStorage / using a new device can't reset
// the gate. Bumps localStorage + dispatches the event so a mounted GuestGate
// re-evaluates. No-op when the server count is lower (local optimistic wins).
export function syncGuestClips(serverCount: number): number {
  if (typeof window === "undefined") return serverCount || 0;
  const merged = Math.max(getGuestClips(), serverCount || 0);
  try {
    localStorage.setItem(CLIPS_KEY, String(merged));
    window.dispatchEvent(new CustomEvent(GUEST_CLIP_EVENT, { detail: merged }));
  } catch {
    /* ignore */
  }
  return merged;
}

export function isGateDismissed(): boolean {
  if (typeof window === "undefined") return false;
  return localStorage.getItem(DISMISSED_KEY) === "1";
}

// Hard gate: true once a guest has watched at least GUEST_HARD_LIMIT clips.
// Independent of the dismissible soft banner — dismissing the nudge does NOT
// dismiss this. Watching is blocked until the guest upgrades to an account.
export function isHardGated(): boolean {
  return getGuestClips() >= GUEST_HARD_LIMIT;
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
