// Component tests for `IntuitionScroll` — the retro reskin of `IntuitionCard`
// (Task 4.3, Req 6.1).
//
// `IntuitionScroll` is a pure presentational reskin: it renders the node hook
// on a parchment scroll, preserving the `IntuitionCard` prop contract (`hook,
// node?, compact?, className?`). These tests pin the one behavior that matters
// for the learning beat — the hook text is rendered verbatim — plus the
// supporting affordances (the node eyebrow label, the empty-hook fallback, and
// the compact caption variant used inside the `ClipCutscene`).

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import IntuitionScroll from "./IntuitionScroll";

afterEach(() => {
  cleanup();
});

describe("IntuitionScroll (Req 6.1)", () => {
  it("renders the hook text verbatim", () => {
    const hook =
      "Think of a hash map as a coat check: you hand over a key and get back exactly your coat.";

    render(<IntuitionScroll hook={hook} />);

    expect(screen.getByText(hook)).toBeInTheDocument();
  });

  it("renders the node name as an eyebrow label when provided", () => {
    render(<IntuitionScroll hook="A short mental model." node="Hash Maps" />);

    expect(screen.getByText("Hash Maps")).toBeInTheDocument();
    // The scroll is labeled for the specific concept for assistive tech.
    expect(
      screen.getByLabelText("Intuition for Hash Maps"),
    ).toBeInTheDocument();
  });

  it("trims surrounding whitespace from the hook", () => {
    render(<IntuitionScroll hook="   Padded idea.   " />);

    expect(screen.getByText("Padded idea.")).toBeInTheDocument();
  });

  it("shows a graceful fallback when the hook is empty", () => {
    render(<IntuitionScroll hook="   " />);

    expect(
      screen.getByText(/no intuition available for this node/i),
    ).toBeInTheDocument();
  });

  it("renders the hook in the compact caption variant used inside the cutscene", () => {
    const hook = "Recursion is a function that calls a smaller version of itself.";

    render(<IntuitionScroll hook={hook} compact />);

    expect(screen.getByText(hook)).toBeInTheDocument();
    expect(screen.getByLabelText("Intuition")).toHaveAttribute(
      "data-compact",
      "true",
    );
  });
});
