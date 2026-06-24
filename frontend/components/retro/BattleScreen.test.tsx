import { describe, it, expect, afterEach } from "vitest";
import { render, screen, within, cleanup } from "@testing-library/react";
import BattleScreen from "./BattleScreen";
import { RetroThemeProvider } from "./RetroThemeProvider";
import { type Band, type DecideResponse } from "@/lib/game-progress";

// Component tests for the retro `BattleScreen` reskin (Task 3.2, Req 5.1–5.4).
//
// Req 5.1: present exactly one Battle outcome screen for the decided band —
//          CLIMB = Monster slain / advance, REINFORCE = stalemate / hold,
//          DESCEND = tactical retreat to train.
// Req 5.2: display the `decide_next` diagnosis text on the rendered screen.
// Req 5.3: frame a DESCEND as a strategic retreat, NEVER as a failure or loss.
// Req 5.4: when the decision reaches the goal, present the Dragon as defeated
//          and signal the quest is complete.
//
// `BattleScreen` consumes `useRetroSettings()` for the reduced-motion gate, so
// every render is wrapped in `RetroThemeProvider`.

// Documented per-band battle framing (mirrors the PRESENTATION map in
// BattleScreen.tsx): eyebrow + headline copy.
const FRAMING_BY_BAND: Record<Band, { eyebrow: string; title: string }> = {
  CLIMB: { eyebrow: "Victory", title: "Monster slain!" },
  REINFORCE: { eyebrow: "Stalemate", title: "Stalemate — hold the line" },
  DESCEND: { eyebrow: "Retreat", title: "Tactical retreat — train up" },
};

// Build a DecideResponse for a band with a band-specific, identifiable diagnosis.
function decisionFor(
  band: Band,
  overrides: Partial<DecideResponse> = {},
): DecideResponse {
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

function renderBattle(decision: DecideResponse) {
  return render(
    <RetroThemeProvider>
      <BattleScreen decision={decision} />
    </RetroThemeProvider>,
  );
}

afterEach(() => {
  cleanup();
});

describe("BattleScreen renders exactly one battle outcome per band (Req 5.1)", () => {
  it.each<Band>(["CLIMB", "REINFORCE", "DESCEND"])(
    "renders a single battle screen tagged with the decided band for %s",
    (band) => {
      renderBattle(decisionFor(band));

      // Exactly one battle outcome screen is rendered.
      const screens = screen.getAllByRole("status");
      expect(screens).toHaveLength(1);

      // It is tagged with the decided band.
      expect(screens[0].getAttribute("data-band")).toBe(band);
    },
  );

  it.each<Band>(["CLIMB", "REINFORCE", "DESCEND"])(
    "shows the documented battle framing for %s",
    (band) => {
      renderBattle(decisionFor(band));

      const battle = screen.getByRole("status");
      expect(within(battle).getByText(FRAMING_BY_BAND[band].eyebrow)).toBeInTheDocument();
      expect(within(battle).getByText(FRAMING_BY_BAND[band].title)).toBeInTheDocument();
    },
  );

  it("selects the framing from the code-computed band, not the LLM action", () => {
    // band (source of truth) = DESCEND, even though action says CLIMB.
    const decision = decisionFor("DESCEND", { action: "CLIMB" });
    renderBattle(decision);

    const battle = screen.getByRole("status");
    expect(battle.getAttribute("data-band")).toBe("DESCEND");
    expect(within(battle).getByText(FRAMING_BY_BAND.DESCEND.title)).toBeInTheDocument();
  });
});

describe("BattleScreen displays the decide_next diagnosis (Req 5.2)", () => {
  it.each<Band>(["CLIMB", "REINFORCE", "DESCEND"])(
    "shows the diagnosis under the Diagnosis label for %s",
    (band) => {
      const decision = decisionFor(band);
      renderBattle(decision);

      // The diagnosis text is rendered verbatim.
      expect(screen.getByText(decision.diagnosis)).toBeInTheDocument();

      // It sits in the dedicated "Diagnosis" panel (prominent, not buried).
      const battle = screen.getByRole("status");
      expect(within(battle).getByText("Diagnosis")).toBeInTheDocument();
    },
  );

  it("falls back to a placeholder when no diagnosis is provided", () => {
    renderBattle(decisionFor("REINFORCE", { diagnosis: "" }));
    expect(screen.getByText(/No diagnosis available/i)).toBeInTheDocument();
  });
});

describe("BattleScreen frames DESCEND as a retreat, never a failure or loss (Req 5.3)", () => {
  it("uses retreat/train framing and avoids failure language", () => {
    renderBattle(decisionFor("DESCEND"));

    const battle = screen.getByRole("status");
    // Strategic-retreat framing is present (eyebrow + headline).
    expect(within(battle).getByText("Retreat")).toBeInTheDocument();
    expect(within(battle).getByText(/Tactical retreat — train up/)).toBeInTheDocument();
    // It is never framed as a failure or defeat.
    expect(battle.textContent ?? "").not.toMatch(/fail/i);
    expect(battle.textContent ?? "").not.toMatch(/defeat/i);
    // The supporting beat labels the destination as a fall-back, not a loss.
    expect(within(battle).getByText("Fall back to")).toBeInTheDocument();
  });
});

describe("BattleScreen goal-reached boss beat (Req 5.4)", () => {
  it("presents the Dragon as defeated and signals the quest is complete on a CLIMB to goal", () => {
    renderBattle(
      decisionFor("CLIMB", { reached_goal: true, next_node: null }),
    );

    const battle = screen.getByRole("status");
    // The boss beat: the Dragon falls and the Keep is cleared.
    expect(within(battle).getByText("The Dragon falls!")).toBeInTheDocument();
    expect(within(battle).getByText(/Keep cleared/i)).toBeInTheDocument();
    // The screen is flagged as the goal-reached beat.
    expect(battle.getAttribute("data-reached-goal")).toBe("true");
  });

  it("does not show the Dragon-falls beat for a non-goal CLIMB", () => {
    renderBattle(decisionFor("CLIMB", { reached_goal: false }));

    const battle = screen.getByRole("status");
    expect(within(battle).queryByText("The Dragon falls!")).not.toBeInTheDocument();
    expect(battle.getAttribute("data-reached-goal")).toBe("false");
  });
});
