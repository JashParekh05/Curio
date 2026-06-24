"use client";

// RetroThemeProvider — the retro presentation context for the Pixel Quest
// Play_Surface reskin (Req 1.3, 12.1, 12.2). It owns the two learner-toggleable
// retro chrome settings and the OS motion preference:
//
//   - crtOn         the optional CRT/scanline overlay toggle (Req 1.3, 12.2)
//   - audioOn       the optional chiptune/SFX toggle (muted by default; Phase 2
//                   wiring lives behind this flag — Req 16.2)
//   - reducedMotion read from `prefers-reduced-motion: reduce` so every retro
//                   animation can render its static end-state (Req 1.4, 12.1)
//
// The provider wraps the `/play` subtree and renders a single retro root element
// carrying `data-reduced-motion` (and `.crt`) so `retro.css` can key its motion
// and overlay rules off the DOM without any JS in the hot path (task 1.2).
//
// Persistence and environment access follow the existing house patterns:
//   - the `prefers-reduced-motion` read is SSR-guarded exactly like
//     `SkillTreeMap`'s `prefersReducedMotion()` (no `matchMedia` → false, never
//     throws), and additionally subscribes to live OS changes;
//   - `crtOn`/`audioOn` persist best-effort to localStorage under `pq_settings`
//     with a `typeof window` guard + try/catch, mirroring the `game-progress`
//     codec (storage being unavailable never breaks the surface).

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";

// localStorage key for the persisted retro chrome settings. Independent of the
// game session codec (`curio_game_session_*`) so toggles survive across quests.
const STORAGE_KEY = "pq_settings";

// The shape persisted to localStorage. `reducedMotion` is intentionally NOT
// persisted — it always reflects the live OS preference (Req 1.4, 12.1).
interface PersistedRetroSettings {
  crtOn: boolean;
  audioOn: boolean;
}

export interface RetroThemeValue {
  // Whether the CRT/scanline overlay is on (learner-toggleable — Req 1.3, 12.2).
  crtOn: boolean;
  // Whether retro chiptune/SFX is enabled. Muted by default (Req 16.2, Phase 2).
  audioOn: boolean;
  // True when the OS asks to reduce motion; gates every retro animation so it
  // renders its static end-state instead (Req 1.4, 12.1).
  reducedMotion: boolean;
  // Flip the CRT overlay and persist the choice (best-effort).
  toggleCrt: () => void;
  // Flip the audio setting and persist the choice (best-effort).
  toggleAudio: () => void;
}

// Sensible defaults so `useRetroSettings()` is safe even outside a provider
// (mirrors the auth-context default-value pattern): CRT overlay OFF by default
// (the scanline/vignette overlay washed out the whole page — it's now opt-in via
// the toggle), audio muted by default, motion enabled, toggles are no-ops.
const DEFAULT_VALUE: RetroThemeValue = {
  crtOn: false,
  audioOn: false,
  reducedMotion: false,
  toggleCrt: () => {},
  toggleAudio: () => {},
};

const RetroThemeContext = createContext<RetroThemeValue>(DEFAULT_VALUE);

// Read persisted retro settings. SSR-guarded and wrapped so storage being
// unavailable (private mode, quota, SSR) yields defaults instead of throwing.
function readPersisted(): Partial<PersistedRetroSettings> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as unknown;
    return parsed && typeof parsed === "object"
      ? (parsed as Partial<PersistedRetroSettings>)
      : {};
  } catch {
    return {};
  }
}

// Persist retro settings best-effort. Never throws (mirrors the game-progress
// codec): on SSR or any storage failure the choice simply stays in memory.
function writePersisted(settings: PersistedRetroSettings): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch {
    /* storage unavailable — keep the in-memory state */
  }
}

export function RetroThemeProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  // Start from defaults on both server and first client render so the markup
  // matches (no hydration mismatch); persisted values + the live motion
  // preference are applied right after mount in the effects below.
  const [crtOn, setCrtOn] = useState(DEFAULT_VALUE.crtOn);
  const [audioOn, setAudioOn] = useState(DEFAULT_VALUE.audioOn);
  const [reducedMotion, setReducedMotion] = useState(false);

  // Hydrate persisted toggles after mount (SSR-safe).
  useEffect(() => {
    const persisted = readPersisted();
    if (typeof persisted.crtOn === "boolean") setCrtOn(persisted.crtOn);
    if (typeof persisted.audioOn === "boolean") setAudioOn(persisted.audioOn);
  }, []);

  // Track the OS motion preference, including live changes mid-session.
  useEffect(() => {
    if (
      typeof window === "undefined" ||
      typeof window.matchMedia !== "function"
    ) {
      return;
    }
    let mql: MediaQueryList;
    try {
      mql = window.matchMedia("(prefers-reduced-motion: reduce)");
    } catch {
      return;
    }
    const sync = () => setReducedMotion(mql.matches);
    sync();
    // `addEventListener` is the modern API; fall back to the deprecated
    // `addListener` for older Safari.
    if (typeof mql.addEventListener === "function") {
      mql.addEventListener("change", sync);
      return () => mql.removeEventListener("change", sync);
    }
    mql.addListener(sync);
    return () => mql.removeListener(sync);
  }, []);

  const toggleCrt = useCallback(() => {
    setCrtOn((prev) => {
      const next = !prev;
      writePersisted({ crtOn: next, audioOn });
      return next;
    });
  }, [audioOn]);

  const toggleAudio = useCallback(() => {
    setAudioOn((prev) => {
      const next = !prev;
      writePersisted({ crtOn, audioOn: next });
      return next;
    });
  }, [crtOn]);

  return (
    <RetroThemeContext.Provider
      value={{ crtOn, audioOn, reducedMotion, toggleCrt, toggleAudio }}
    >
      {/* The retro root: `retro.css` keys its motion/overlay rules off these
          attributes so styling needs no JS on the hot path (task 1.2). */}
      <div
        className={`pixel-quest${crtOn ? " crt" : ""}`}
        data-reduced-motion={reducedMotion ? "true" : "false"}
        data-crt={crtOn ? "true" : "false"}
      >
        {children}
      </div>
    </RetroThemeContext.Provider>
  );
}

// Access the retro theme settings + toggles. Safe to call outside a provider
// (returns the defaults above), mirroring `useAuth()`.
export function useRetroSettings(): RetroThemeValue {
  return useContext(RetroThemeContext);
}
