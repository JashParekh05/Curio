"use client";

// RetroAudioToggle — a small, self-contained control that flips the optional
// chiptune/SFX setting on the Pixel Quest Play_Surface (Req 16.1, 16.2, 12.3).
//
// It is a thin button over `useRetroSettings().toggleAudio`, the existing audio
// flag on RetroThemeProvider (muted by default — Req 16.2). Pressing it is the
// user gesture that `useRetroAudio` uses to start sound, so audio can never play
// before the learner asks for it. The button is keyboard-operable and labeled
// (Req 12.3): it is a native <button> carrying `aria-pressed` + an `aria-label`
// that reflects the current state.
//
// This is purely presentational chrome the page can drop next to the CRT toggle;
// the actual audio engine lives in `useRetroAudio`. Mounting this component does
// NOT create an AudioContext or make any sound on its own.

import { useRetroSettings } from "./RetroThemeProvider";

export interface RetroAudioToggleProps {
  // Optional extra classes for layout composition by the parent surface.
  className?: string;
}

export default function RetroAudioToggle({
  className = "",
}: RetroAudioToggleProps) {
  const { audioOn, toggleAudio } = useRetroSettings();

  return (
    <button
      type="button"
      onClick={toggleAudio}
      aria-pressed={audioOn}
      aria-label={audioOn ? "Mute retro sound" : "Enable retro sound"}
      title={audioOn ? "Mute retro sound" : "Enable retro sound"}
      className={`pixel-font pixelated ${className}`}
      style={{
        background: audioOn ? "var(--pq-lime)" : "var(--pq-panel)",
        color: "var(--pq-ink)",
        border: "3px solid var(--pq-ink)",
        boxShadow: "3px 3px 0 0 var(--pq-ink)",
        padding: "0.35rem 0.55rem",
        fontSize: "0.5rem",
        textTransform: "uppercase",
        letterSpacing: "0.1em",
        cursor: "pointer",
        lineHeight: 1,
      }}
    >
      {/* A glyph + word so the state reads at a glance; the accessible name is
          carried by aria-label above. */}
      <span aria-hidden="true">{audioOn ? "♪ " : "× "}</span>
      {audioOn ? "Sound On" : "Sound Off"}
    </button>
  );
}
