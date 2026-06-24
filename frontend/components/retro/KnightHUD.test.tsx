// Component tests for `KnightHUD` — the retro reskin of `XpHud`
// (Task 5.4, Req 7.1, 7.2).
//
// Two guarantees are pinned here:
//
//   1. Rank + XP display (Req 7.1) — the arcade status bar surfaces the
//      learner's rank (level) and their XP position within that rank, derived
//      from the shared `xpLevelStats` curve so the HUD and `XpHud` agree.
//   2. Award beat on XP gain (Req 7.2) — when the XP total rises between
//      renders the HUD flashes a transient "+N XP" award badge, and the rank
//      display updates when the gain crosses a level boundary.
//
// KnightHUD reads `useRetroSettings()`, so every render is wrapped in
// `RetroThemeProvider` (the provider default is safe outside, but wrapping
// mirrors how the Play_Surface mounts it).

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

import KnightHUD from "./KnightHUD";
import { RetroThemeProvider } from "./RetroThemeProvider";
import { xpLevelStats } from "../XpHud";

function renderHud(props: { xp: number; level?: number; compact?: boolean }) {
  return render(
    <RetroThemeProvider>
      <KnightHUD {...props} />
    </RetroThemeProvider>,
  );
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe("KnightHUD shows rank + XP (Req 7.1)", () => {
  it("renders the derived rank and the XP position within the rank", () => {
    // xp=50 → level 1, 50 XP into a 100 XP rank.
    const stats = xpLevelStats(50);
    expect(stats.level).toBe(1);

    renderHud({ xp: 50 });

    // The status bar is labeled for assistive tech.
    expect(
      screen.getByLabelText("Knight rank and experience"),
    ).toBeInTheDocument();

    // The rank plate names the derived level.
    expect(screen.getByLabelText(`Rank ${stats.level}`)).toBeInTheDocument();

    // The XP gauge shows progress into the current rank.
    expect(
      screen.getByText(`${stats.xpIntoLevel} / ${stats.xpForLevel}`),
    ).toBeInTheDocument();

    // The gauge exposes its progress to assistive tech.
    const gauge = screen.getByRole("progressbar");
    expect(gauge.getAttribute("aria-valuenow")).toBe(
      String(stats.xpIntoLevel),
    );
    expect(gauge.getAttribute("aria-valuemax")).toBe(String(stats.xpForLevel));
  });

  it("prefers an explicit level over the derived one", () => {
    // xp derives to level 1, but the parent passes an explicit rank of 4.
    renderHud({ xp: 50, level: 4 });
    expect(screen.getByLabelText("Rank 4")).toBeInTheDocument();
  });
});

describe("KnightHUD award beat on XP gain (Req 7.2)", () => {
  it("flashes a +N XP badge when the XP total rises between renders", async () => {
    const { rerender } = renderHud({ xp: 20 });

    // No award beat on the initial render.
    expect(screen.queryByText(/\+\d+ XP/)).not.toBeInTheDocument();

    // The learner completes a checkpoint and gains 50 XP.
    rerender(
      <RetroThemeProvider>
        <KnightHUD xp={70} />
      </RetroThemeProvider>,
    );

    const award = await screen.findByText("+50 XP");
    expect(award).toBeInTheDocument();
    expect(award.getAttribute("role")).toBe("status");
  });

  it("updates the rank display when a gain crosses a level boundary", () => {
    const { rerender } = renderHud({ xp: 50 });
    expect(screen.getByLabelText("Rank 1")).toBeInTheDocument();

    // 100 XP clears the 100 XP cost of rank 1 → rank 2.
    const stats = xpLevelStats(100);
    expect(stats.level).toBe(2);

    rerender(
      <RetroThemeProvider>
        <KnightHUD xp={100} />
      </RetroThemeProvider>,
    );

    expect(screen.getByLabelText("Rank 2")).toBeInTheDocument();
  });
});
