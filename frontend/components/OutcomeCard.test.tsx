import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import OutcomeCard from "./OutcomeCard";
import { type Band, type DecideResponse } from "@/lib/game-progress";

// Component tests for the roguelike outcome cards (Task 9.6, Req 20.2–20.4).
//
// Req 20.2: display one of three outcome cards for CLIMB / REINFORCE / DESCEND.
// Req 20.3: frame DESCEND as a roguelike fall-back, not a failure.
// Req 20.4: display the Diagnosis returned by `decide_next` on the relevant card.

// Documented accent fill per band (mirrors the Skill_Tree_Map mapping).
const ACCENT_BY_BAND: Record<Band, string> = {
  CLIMB: "bg-accent-lime",
  REINFORCE: "bg-accent-cyan",
  DESCEND: "bg-accent-orange",
};

// Build a DecideResponse for a band with a band-specific, identifiable diagnosis.
function decisionFor(band: Band, overrides: Partial<DecideResponse> = {}): DecideResponse {
  return {
    action: band,
    band,
    next_node: "Pointers and Memory",
    reached_goal: false,
    gap: "base-case reasoning",
    diagnosis: `Diagnosis for ${band}: you confused the base case with the recursive step.`,
    score_pct: 0.5,
    missed_concepts: ["recursion"],
    correct_concepts: ["iteration"],
    ...overrides,
  };
}

describe("OutcomeCard displays the decide_next diagnosis on the rendered card (Req 20.4)", () => {
  it.each<Band>(["CLIMB", "REINFORCE", "DESCEND"])(
    "shows the diagnosis prominently under the Diagnosis label for %s",
    (band) => {
      const decision = decisionFor(band);
      render(<OutcomeCard decision={decision} />);

      // The diagnosis text is rendered verbatim.
      const diag = screen.getByText(decision.diagnosis);
      expect(diag).toBeInTheDocument();

      // It sits in the dedicated "Diagnosis" panel (prominent, not buried).
      const card = screen.getByRole("status");
      expect(within(card).getByText("Diagnosis")).toBeInTheDocument();
    },
  );

  it("falls back to a placeholder when no diagnosis is provided", () => {
    render(<OutcomeCard decision={decisionFor("REINFORCE", { diagnosis: "" })} />);
    expect(
      screen.getByText(/No diagnosis available/i),
    ).toBeInTheDocument();
  });
});

describe("OutcomeCard renders exactly one of the three outcome cards (Req 20.2)", () => {
  it.each<Band>(["CLIMB", "REINFORCE", "DESCEND"])(
    "renders a single card with the documented accent for %s",
    (band) => {
      render(<OutcomeCard decision={decisionFor(band)} />);

      // Exactly one outcome card is rendered.
      const cards = screen.getAllByRole("status");
      expect(cards).toHaveLength(1);

      // The card is tagged with the decided band and carries its accent fill.
      const card = cards[0];
      expect(card.getAttribute("data-band")).toBe(band);
      expect(card.className).toContain(ACCENT_BY_BAND[band]);
    },
  );

  it("selects the card from the code-computed band, not the LLM action", () => {
    // band (source of truth) = DESCEND, even though action says CLIMB.
    const decision = decisionFor("DESCEND", { action: "CLIMB" });
    render(<OutcomeCard decision={decision} />);

    const card = screen.getByRole("status");
    expect(card.getAttribute("data-band")).toBe("DESCEND");
    expect(card.className).toContain(ACCENT_BY_BAND.DESCEND);
  });
});

describe("OutcomeCard frames DESCEND as a roguelike fall-back, not a failure (Req 20.3)", () => {
  it("uses fall-back framing and avoids failure language", () => {
    render(<OutcomeCard decision={decisionFor("DESCEND")} />);

    const card = screen.getByRole("status");
    // Roguelike fall-back framing is present.
    expect(within(card).getByText(/Fall-back/i)).toBeInTheDocument();
    // It is never framed as a failure.
    expect(card.textContent ?? "").not.toMatch(/fail/i);
    // DESCEND uses the orange fall-back accent, not the pink wrong-answer beat.
    expect(card.className).toContain("bg-accent-orange");
    expect(card.className).not.toContain("bg-accent-pink");
  });
});

describe("OutcomeCard goal-reached beat (Req 20.2 CLIMB)", () => {
  it("flags the boss-cleared beat when a CLIMB reaches the goal", () => {
    render(
      <OutcomeCard
        decision={decisionFor("CLIMB", { reached_goal: true, next_node: null })}
      />,
    );
    expect(screen.getByText(/Goal reached/i)).toBeInTheDocument();
    expect(screen.getByText(/Boss cleared/i)).toBeInTheDocument();
  });
});
