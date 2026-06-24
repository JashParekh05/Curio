// Tests for `RetroAudioToggle` — the chrome control that flips the optional
// chiptune/SFX setting on the Pixel Quest Play_Surface (Task 10.4, Req 16.2,
// 12.3).
//
// Guarantees pinned here:
//   1. Muted by default (Req 16.2) — the toggle reflects the provider's default
//      muted state: `aria-pressed="false"` and a "Sound Off" / "Enable retro
//      sound" affordance.
//   2. Toggle reflects state — clicking flips the setting on (and off again),
//      with `aria-pressed` and the label tracking the state. It is a native,
//      labeled <button> (keyboard-operable, Req 12.3).
//   3. The toggle is pure chrome — mounting and clicking it never constructs an
//      AudioContext on its own (the engine lives in `useRetroAudio`).

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { RetroThemeProvider } from "./RetroThemeProvider";
import RetroAudioToggle from "./RetroAudioToggle";

// Counting fake so we can assert the toggle never builds an audio engine.
let audioContextConstructions = 0;
class FakeAudioContext {
  constructor() {
    audioContextConstructions += 1;
  }
}

beforeEach(() => {
  audioContextConstructions = 0;
  vi.stubGlobal("AudioContext", FakeAudioContext);
});

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

function renderToggle() {
  return render(
    <RetroThemeProvider>
      <RetroAudioToggle />
    </RetroThemeProvider>,
  );
}

describe("RetroAudioToggle muted by default (Req 16.2)", () => {
  it("renders muted: aria-pressed false, labeled to enable sound", () => {
    renderToggle();

    const btn = screen.getByRole("button", { name: "Enable retro sound" });
    expect(btn).toHaveAttribute("aria-pressed", "false");
    expect(btn.textContent).toContain("Sound Off");
    // Pure chrome — mounting must not create an audio engine.
    expect(audioContextConstructions).toBe(0);
  });
});

describe("RetroAudioToggle reflects state on click (Req 12.3)", () => {
  it("flips to on then back to off, with aria-pressed and label tracking state", () => {
    renderToggle();

    // Default off.
    let btn = screen.getByRole("button", { name: "Enable retro sound" });
    expect(btn).toHaveAttribute("aria-pressed", "false");

    // Click → on.
    fireEvent.click(btn);
    btn = screen.getByRole("button", { name: "Mute retro sound" });
    expect(btn).toHaveAttribute("aria-pressed", "true");
    expect(btn.textContent).toContain("Sound On");

    // Click → off again.
    fireEvent.click(btn);
    btn = screen.getByRole("button", { name: "Enable retro sound" });
    expect(btn).toHaveAttribute("aria-pressed", "false");
    expect(btn.textContent).toContain("Sound Off");

    // The toggle itself never instantiates the audio engine.
    expect(audioContextConstructions).toBe(0);
  });
});
