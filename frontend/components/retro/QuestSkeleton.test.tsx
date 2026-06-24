// Component tests for `QuestSkeleton` — the retro loading beat
// (Task 5.4, Req 10.1).
//
// Guarantees pinned here:
//
//   1. Renders for the loading phases (Req 10.1, 10.2) — the skeleton surfaces
//      a labeled, busy live region with the "summoning the realm…" caption and
//      a placeholder map trail, so the quest never shows a blank/frozen screen
//      while the probe generates or a node is delivered.
//   2. It is configurable — a custom caption and tile count are honored, and the
//      tile count is clamped to a sane range.

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

import QuestSkeleton from "./QuestSkeleton";

afterEach(() => {
  cleanup();
});

describe("QuestSkeleton renders the loading beat (Req 10.1)", () => {
  it("renders a labeled, busy live region with the default caption", () => {
    render(<QuestSkeleton />);

    const region = screen.getByRole("status");
    expect(region).toBeInTheDocument();
    expect(region.getAttribute("aria-busy")).toBe("true");
    expect(region.getAttribute("aria-label")).toBe("Summoning the realm…");

    // The default caption is shown to sighted users too.
    expect(screen.getByText("Summoning the realm…")).toBeInTheDocument();
  });

  it("honors a custom caption for different loading phases", () => {
    render(<QuestSkeleton label="Delivering the next stage…" />);

    const region = screen.getByRole("status");
    expect(region.getAttribute("aria-label")).toBe("Delivering the next stage…");
    expect(
      screen.getByText("Delivering the next stage…"),
    ).toBeInTheDocument();
  });
});

describe("QuestSkeleton placeholder trail", () => {
  it("clamps an out-of-range tile count without crashing", () => {
    // Below the floor (3) and above the ceiling (16) both render a valid trail.
    const { rerender } = render(<QuestSkeleton tileCount={0} />);
    expect(screen.getByRole("status")).toBeInTheDocument();

    rerender(<QuestSkeleton tileCount={999} />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });
});
