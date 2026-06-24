// Tests for `useRetroAudio` — optional chiptune/SFX engine for the Pixel Quest
// Play_Surface (Task 10.4, Req 16.2).
//
// Two guarantees are pinned here:
//
//   1. Muted by default + autoplay safety (Req 16.2) — the hook starts with
//      `audioOn === false`, and while muted it NEVER constructs an AudioContext
//      and NEVER plays a sound (so nothing autoplays without a user gesture).
//      Mounting the hook is inert and never throws.
//   2. Toggle reflects state — flipping the audio setting (a user gesture)
//      through the provider flips `audioOn` to true, and flipping it back mutes
//      again. The hook stays no-throw across the transitions.
//
// jsdom has no real AudioContext; we install a counting fake so we can assert it
// is NOT constructed on mount/while muted.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, act } from "@testing-library/react";
import { RetroThemeProvider, useRetroSettings } from "./RetroThemeProvider";
import { useRetroAudio } from "./useRetroAudio";

// ---------------------------------------------------------------------------
// A minimal, counting fake AudioContext so we can detect construction. jsdom
// provides none, so without this the hook is simply inert; installing it lets
// us assert "not constructed on mount" with a real spy.
// ---------------------------------------------------------------------------
let audioContextConstructions = 0;

class FakeParam {
  value = 0;
  setValueAtTime() {}
  exponentialRampToValueAtTime() {}
}
class FakeGain {
  gain = new FakeParam();
  connect() {}
}
class FakeOscillator {
  type = "square";
  frequency = new FakeParam();
  connect() {}
  start() {}
  stop() {}
}
class FakeAudioContext {
  state: "suspended" | "running" | "closed" = "suspended";
  currentTime = 0;
  destination = {};
  constructor() {
    audioContextConstructions += 1;
  }
  createGain() {
    return new FakeGain();
  }
  createOscillator() {
    return new FakeOscillator();
  }
  resume() {
    this.state = "running";
    return Promise.resolve();
  }
  close() {
    this.state = "closed";
    return Promise.resolve();
  }
}

// A harness that surfaces the hook's `audioOn` plus the provider's toggle so the
// test can drive and observe the real hook through the real context.
function Harness() {
  const { audioOn, playMove, play } = useRetroAudio();
  const { toggleAudio } = useRetroSettings();
  return (
    <div>
      <span data-testid="audio-on">{String(audioOn)}</span>
      <button data-testid="toggle" onClick={toggleAudio}>
        toggle
      </button>
      <button data-testid="play-move" onClick={playMove}>
        move
      </button>
      <button data-testid="play-win" onClick={() => play("battle-win")}>
        win
      </button>
    </div>
  );
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

describe("useRetroAudio muted-by-default + autoplay safety (Req 16.2)", () => {
  it("starts with audioOn=false and constructs no AudioContext on mount", () => {
    render(
      <RetroThemeProvider>
        <Harness />
      </RetroThemeProvider>,
    );

    expect(screen.getByTestId("audio-on").textContent).toBe("false");
    // Nothing may autoplay: no AudioContext is created just by mounting muted.
    expect(audioContextConstructions).toBe(0);
  });

  it("ignores SFX triggers while muted — no sound, no AudioContext, no throw", () => {
    render(
      <RetroThemeProvider>
        <Harness />
      </RetroThemeProvider>,
    );

    expect(() => {
      fireEvent.click(screen.getByTestId("play-move"));
      fireEvent.click(screen.getByTestId("play-win"));
    }).not.toThrow();

    // SFX are a hard no-op while muted: still no context, still silent.
    expect(audioContextConstructions).toBe(0);
    expect(screen.getByTestId("audio-on").textContent).toBe("false");
  });

  it("is inert and never throws when AudioContext is unavailable (SSR/jsdom)", () => {
    // Remove the fake entirely so getAudioContextCtor() resolves to null.
    vi.stubGlobal("AudioContext", undefined);

    expect(() =>
      render(
        <RetroThemeProvider>
          <Harness />
        </RetroThemeProvider>,
      ),
    ).not.toThrow();

    expect(screen.getByTestId("audio-on").textContent).toBe("false");
    expect(() =>
      fireEvent.click(screen.getByTestId("play-move")),
    ).not.toThrow();
  });
});

describe("useRetroAudio toggle reflects state", () => {
  it("flips audioOn to true on toggle (a user gesture) and back to false", () => {
    render(
      <RetroThemeProvider>
        <Harness />
      </RetroThemeProvider>,
    );

    expect(screen.getByTestId("audio-on").textContent).toBe("false");

    // Toggling on is a deliberate user gesture — audioOn flips true.
    act(() => {
      fireEvent.click(screen.getByTestId("toggle"));
    });
    expect(screen.getByTestId("audio-on").textContent).toBe("true");

    // Toggling back mutes again.
    act(() => {
      fireEvent.click(screen.getByTestId("toggle"));
    });
    expect(screen.getByTestId("audio-on").textContent).toBe("false");
  });
});
