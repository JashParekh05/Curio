// One-time intro/demo carousel tracking. Stored in localStorage so it shows
// once per device for both guests and accounts (no server round-trip, no
// added login latency).

const INTRO_KEY = "curio_intro_seen_v1";

export function hasSeenIntro(): boolean {
  if (typeof window === "undefined") return true; // never gate during SSR
  try {
    return localStorage.getItem(INTRO_KEY) === "1";
  } catch {
    return true; // storage blocked — don't trap the user on the intro
  }
}

export function markIntroSeen(): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(INTRO_KEY, "1");
  } catch {
    // ignore — worst case the intro shows again next visit
  }
}
