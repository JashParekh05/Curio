"use client";

// QuestIntro — the opening narrative beat for the Pixel Quest reskin (Req 4.1,
// 4.2, 4.3, 4.4). Shown once after a session starts, it frames the entered
// topic as a Dragon to be slain and the learner as the Knight, and teaches the
// adaptive loop in adventure language before the probe begins.
//
// Per the design (`QuestIntro` in Components and Interfaces) the copy is a
// purely client-side template — there is NO extra LLM/network call. The
// narrative is woven from the `goal` string the learner already entered so it
// always names the actual subject (Req 4.2). The beat is skippable via the
// `onSkip` affordance so a returning learner is never blocked (Req 4.4); the
// page owns the once-per-session "seen" flag (task 6.2).
//
// Styling stays in the retro theme layer: `--pq-*` tokens, the `.pixel-font`
// chrome face, and the legible `.pixel-body` face for the longer narrative
// (Req 1.5). Both actions are real, labeled <button>s so the beat is fully
// keyboard-operable (Req 12.3).

export interface QuestIntroProps {
  // The topic the learner entered — the Dragon's hoard. Woven into the
  // narrative so the story names the actual subject (Req 4.2).
  goal: string;
  // Begin the quest (advance into the placement probe).
  onBegin: () => void;
  // Skip the intro — a returning learner is never blocked from playing (Req 4.4).
  onSkip: () => void;
}

/**
 * Renders the templated quest-intro narrative. Presentational + client-side
 * only: no LLM call, all copy derived from `goal`. The parent owns when it is
 * shown and the per-session "seen" flag.
 */
export default function QuestIntro({ goal, onBegin, onSkip }: QuestIntroProps) {
  // Fall back gracefully if the topic is somehow empty/whitespace so the
  // narrative still reads (the page rejects empty topics upstream — Req 9.2).
  const topic = goal?.trim() || "the unknown";

  return (
    <section
      className="pixel-quest relative w-full max-w-2xl mx-auto"
      aria-label="Quest introduction"
    >
      <div
        className="px-5 py-6 sm:px-8 sm:py-8"
        style={{
          background: "var(--pq-panel)",
          color: "var(--pq-text)",
          border: "4px solid var(--pq-ink)",
          boxShadow: "6px 6px 0 0 rgba(0, 0, 0, 0.45)",
        }}
      >
        {/* Eyebrow — frames the screen as the quest's opening scroll. */}
        <div className="flex items-center gap-2 mb-4">
          <span aria-hidden className="pixelated text-base leading-none">
            🐉
          </span>
          <span
            className="pixel-font uppercase"
            style={{ fontSize: "0.5rem", letterSpacing: "0.15em" }}
          >
            A New Quest
          </span>
        </div>

        {/* Title — names the Dragon as the topic (Req 4.1, 4.2). */}
        <h1
          className="pixel-font mb-5"
          style={{
            fontSize: "1rem",
            lineHeight: 1.5,
            color: "var(--pq-gold)",
          }}
        >
          The Dragon of {topic}
        </h1>

        {/* Narrative — the Dragon hoards the topic; the learner is the Knight
            (Req 4.1, 4.2). Pure client-side template, no LLM call. */}
        <div className="space-y-3">
          <p className="pixel-body">
            Deep in the Keep, a Dragon hoards the secrets of{" "}
            <strong style={{ color: "var(--pq-gold)" }}>{topic}</strong>. None
            who fear it have ever claimed its treasure.
          </p>
          <p className="pixel-body">
            Take up your sword, Knight. The road is long, but every monster you
            best along the way brings you closer to the Dragon&apos;s lair.
          </p>
        </div>

        {/* The loop, in adventure language (Req 4.3): descend = retreat to
            train, reinforce = hold the line, climb = advance, goal = slay the
            Dragon. Rendered as a labeled legend so the framing is explicit. */}
        <ul
          className="mt-6 mb-6 space-y-2"
          aria-label="How the quest works"
        >
          <li className="pixel-body flex gap-2">
            <span aria-hidden style={{ color: "var(--pq-cyan)" }}>
              ⚔️
            </span>
            <span>
              <strong style={{ color: "var(--pq-cyan)" }}>Advance</strong> —
              best a monster and climb onward toward the Keep.
            </span>
          </li>
          <li className="pixel-body flex gap-2">
            <span aria-hidden style={{ color: "var(--pq-lime)" }}>
              🛡️
            </span>
            <span>
              <strong style={{ color: "var(--pq-lime)" }}>Hold the line</strong>{" "}
              — stand your ground and steady a skill before you press on.
            </span>
          </li>
          <li className="pixel-body flex gap-2">
            <span aria-hidden style={{ color: "var(--pq-orange)" }}>
              🏕️
            </span>
            <span>
              <strong style={{ color: "var(--pq-orange)" }}>
                Retreat to train
              </strong>{" "}
              — fall back down the trail to sharpen a foundational skill. This is
              strategy, never defeat.
            </span>
          </li>
          <li className="pixel-body flex gap-2">
            <span aria-hidden style={{ color: "var(--pq-red)" }}>
              🐉
            </span>
            <span>
              <strong style={{ color: "var(--pq-red)" }}>Slay the Dragon</strong>{" "}
              — reach the Keep and claim the treasure of {topic}.
            </span>
          </li>
        </ul>

        {/* Actions — both are real, labeled buttons for keyboard use (Req 12.3).
            Begin starts the quest; Skip lets a returning learner jump in (Req
            4.4). */}
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={onBegin}
            className="pixel-font uppercase px-5 py-3"
            style={{
              fontSize: "0.625rem",
              letterSpacing: "0.1em",
              background: "var(--pq-gold)",
              color: "var(--pq-ink)",
              border: "3px solid var(--pq-ink)",
              boxShadow: "3px 3px 0 0 rgba(0, 0, 0, 0.45)",
              cursor: "pointer",
            }}
          >
            Take up the sword
          </button>
          <button
            type="button"
            onClick={onSkip}
            className="pixel-font uppercase px-4 py-3"
            style={{
              fontSize: "0.625rem",
              letterSpacing: "0.1em",
              background: "transparent",
              color: "var(--pq-text)",
              border: "3px solid var(--pq-locked)",
              cursor: "pointer",
            }}
          >
            Skip intro
          </button>
        </div>
      </div>
    </section>
  );
}
