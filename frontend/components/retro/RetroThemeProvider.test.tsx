// # Feature: pixel-quest-adventure, Property 3: Reduced motion disables all new animation
//
// Component / property test for `RetroThemeProvider` + `useRetroSettings`
// (Task 1.3, Req 1.4, 12.1, 12.2).
//
// Two guarantees are pinned here:
//
//   1. Reduced motion (Req 1.4, 12.1) — WHERE the OS reports
//      `prefers-reduced-motion: reduce`, the provider surfaces `reducedMotion =
//      true` and stamps `data-reduced-motion="true"` on the retro root so
//      `retro.css` can disable every new animation purely from the DOM. When the
//      OS does not ask for reduced motion (or `matchMedia` is unavailable), the
//      flag is false and the attribute is "false".
//
//   2. CRT toggle persistence (Req 1.3, 12.2) — toggling the CRT/scanline
//      overlay flips `crtOn`, reflects it on the root (`data-crt`), and persists
//      the chrome settings to localStorage under `pq_settings`. This is verified
//      as a *property*: for ANY sequence of CRT/audio toggles, the displayed
//      state, the root attribute, and the persisted `pq_settings` blob stay in
//      lockstep with the expected parity of the toggle sequence.

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import fc from "fast-check";
import { RetroThemeProvider, useRetroSettings } from "./RetroThemeProvider";

// Mirrors the private STORAGE_KEY in RetroThemeProvider.
const STORAGE_KEY = "pq_settings";

// A tiny consumer that surfaces the context values as text + exposes the
// toggles as buttons, so the test can drive and observe the provider.
function Harness() {
  const { crtOn, audioOn, reducedMotion, toggleCrt, toggleAudio } =
    useRetroSettings();
  return (
    <div>
      <span data-testid="crt">{String(crtOn)}</span>
      <span data-testid="audio">{String(audioOn)}</span>
      <span data-testid="rm">{String(reducedMotion)}</span>
      <button data-testid="toggle-crt" onClick={toggleCrt}>
        crt
      </button>
      <button data-testid="toggle-audio" onClick={toggleAudio}>
        audio
      </button>
    </div>
  );
}

function readPersisted(): { crtOn: boolean; audioOn: boolean } | null {
  const raw = window.localStorage.getItem(STORAGE_KEY);
  return raw ? JSON.parse(raw) : null;
}

function retroRoot(): HTMLElement {
  const root = document.querySelector(".pixel-quest") as HTMLElement;
  expect(root).not.toBeNull();
  return root;
}

// Build a matchMedia stub whose `(prefers-reduced-motion: reduce)` query
// reports `matches`. Shaped like the descent test's stub.
function stubMatchMedia(matches: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockReturnValue({
      matches,
      media: "(prefers-reduced-motion: reduce)",
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
      onchange: null,
    }),
  );
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

describe("RetroThemeProvider reduced motion (Req 1.4, 12.1)", () => {
  it("reports reducedMotion=true and stamps data-reduced-motion when the OS prefers reduced motion", () => {
    stubMatchMedia(true);

    render(
      <RetroThemeProvider>
        <Harness />
      </RetroThemeProvider>,
    );

    expect(screen.getByTestId("rm").textContent).toBe("true");
    expect(retroRoot().getAttribute("data-reduced-motion")).toBe("true");
  });

  it("reports reducedMotion=false and data-reduced-motion='false' when motion is allowed", () => {
    stubMatchMedia(false);

    render(
      <RetroThemeProvider>
        <Harness />
      </RetroThemeProvider>,
    );

    expect(screen.getByTestId("rm").textContent).toBe("false");
    expect(retroRoot().getAttribute("data-reduced-motion")).toBe("false");
  });
});

describe("RetroThemeProvider CRT toggle persistence (Req 1.3, 12.2)", () => {
  it("flips crtOn, reflects it on the root, and persists to pq_settings", () => {
    render(
      <RetroThemeProvider>
        <Harness />
      </RetroThemeProvider>,
    );

    // Default: CRT on for the arcade look, nothing persisted yet.
    expect(screen.getByTestId("crt").textContent).toBe("true");
    expect(retroRoot().getAttribute("data-crt")).toBe("true");

    fireEvent.click(screen.getByTestId("toggle-crt"));

    expect(screen.getByTestId("crt").textContent).toBe("false");
    expect(retroRoot().getAttribute("data-crt")).toBe("false");
    expect(readPersisted()).toEqual({ crtOn: false, audioOn: false });
  });
});

describe("Property 3: retro chrome state matches persistence across any toggle sequence", () => {
  it("keeps displayed state, root attribute, and pq_settings in lockstep for any toggle sequence", () => {
    fc.assert(
      fc.property(
        fc.array(fc.constantFrom("crt", "audio"), { maxLength: 12 }),
        (cmds) => {
          window.localStorage.clear();
          const { unmount } = render(
            <RetroThemeProvider>
              <Harness />
            </RetroThemeProvider>,
          );
          try {
            // Provider defaults: CRT on, audio muted (Req 16.2).
            let expectedCrt = true;
            let expectedAudio = false;

            for (const cmd of cmds) {
              if (cmd === "crt") {
                fireEvent.click(screen.getByTestId("toggle-crt"));
                expectedCrt = !expectedCrt;
              } else {
                fireEvent.click(screen.getByTestId("toggle-audio"));
                expectedAudio = !expectedAudio;
              }

              // Displayed context state tracks the toggle parity.
              expect(screen.getByTestId("crt").textContent).toBe(
                String(expectedCrt),
              );
              expect(screen.getByTestId("audio").textContent).toBe(
                String(expectedAudio),
              );

              // The root attribute mirrors the CRT setting for retro.css.
              expect(retroRoot().getAttribute("data-crt")).toBe(
                String(expectedCrt),
              );

              // Every toggle persists the full chrome blob to pq_settings.
              expect(readPersisted()).toEqual({
                crtOn: expectedCrt,
                audioOn: expectedAudio,
              });
            }
          } finally {
            unmount();
            window.localStorage.clear();
          }
        },
      ),
      { numRuns: 50 },
    );
  });
});
