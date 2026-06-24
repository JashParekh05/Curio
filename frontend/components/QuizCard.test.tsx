import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import {
  QuizQuestionList,
  pointsFor,
  POINTS_PER_CORRECT,
  STREAK_BONUS_PER,
  MAX_STREAK_BONUS,
  type AnswerState,
} from "./QuizCard";
import { type QuizQuestion } from "@/lib/api";

// No-behavior-change test for the QuizCard extraction (Task 7.3, Req 22.5).
//
// QuizQuestionList, the brutalist option-state styling (optionClass), and the
// pointsFor/POINTS_PER_CORRECT/STREAK_BONUS_PER/MAX_STREAK_BONUS scoring were
// moved verbatim out of SoftCheckpointCard.tsx into QuizCard.tsx. These tests
// pin the exact option-state classes and the scoring formula so any drift from
// the pre-extraction behavior is caught.

const QUESTIONS: QuizQuestion[] = [
  {
    id: "q1",
    question: "What is 2 + 2?",
    options: ["4", "5", "22"],
    correct_index: 0,
    explanation: "Two plus two is four.",
  },
  {
    id: "q2",
    question: "Capital of France?",
    options: ["Paris", "Rome", "Berlin"],
    correct_index: 0,
    explanation: "Paris is the capital of France.",
  },
];

function buttonFor(qIndex: number, optIndex: number): HTMLButtonElement {
  // QuizQuestionList renders each question's options as buttons in order, so
  // the flat button list maps directly to (question, option) pairs.
  const all = screen.getAllByRole("button");
  return all[qIndex * 3 + optIndex] as HTMLButtonElement;
}

describe("pointsFor scoring (preserved verbatim from SoftCheckpointCard)", () => {
  it("exposes the same scoring constants", () => {
    expect(POINTS_PER_CORRECT).toBe(10);
    expect(STREAK_BONUS_PER).toBe(5);
    expect(MAX_STREAK_BONUS).toBe(40);
  });

  it("awards base points with no streak", () => {
    expect(pointsFor(0)).toBe(10);
  });

  it("adds a per-streak bonus that grows linearly", () => {
    expect(pointsFor(1)).toBe(15); // 10 + 1*5
    expect(pointsFor(2)).toBe(20); // 10 + 2*5
    expect(pointsFor(4)).toBe(30); // 10 + 4*5
    expect(pointsFor(7)).toBe(45); // 10 + 7*5
  });

  it("caps the streak bonus at MAX_STREAK_BONUS", () => {
    expect(pointsFor(8)).toBe(50); // 10 + min(8*5, 40) = 50
    expect(pointsFor(20)).toBe(50); // bonus capped at 40
    expect(pointsFor(1000)).toBe(50);
  });

  it("clamps negative streaks to the base award", () => {
    expect(pointsFor(-1)).toBe(10);
    expect(pointsFor(-100)).toBe(10);
  });

  it("matches the closed-form formula across a sweep of streaks", () => {
    for (let s = -3; s <= 15; s++) {
      const expected =
        POINTS_PER_CORRECT +
        Math.min(Math.max(0, s) * STREAK_BONUS_PER, MAX_STREAK_BONUS);
      expect(pointsFor(s)).toBe(expected);
    }
  });
});

describe("QuizQuestionList option states (Friendly Pop)", () => {
  it("renders unanswered options as enabled with the neutral/hover state", () => {
    render(
      <QuizQuestionList questions={QUESTIONS} answers={{}} onAnswer={() => {}} />,
    );

    const opt = buttonFor(0, 0);
    expect(opt).toBeEnabled();
    expect(opt.className).toContain("border-outline");
    expect(opt.className).toContain("bg-surface");
    expect(opt.className).toContain("text-on-surface");
    expect(opt.className).toContain("hover:border-primary");
    // No explanation is shown until the question is answered.
    expect(screen.queryByText("Two plus two is four.")).toBeNull();
  });

  it("applies lime/pink/dimmed states once a question is answered wrong", () => {
    // q1 answered with the wrong option (chose index 1, correct is index 0).
    const answers: Record<string, AnswerState> = {
      q1: { chosen: 1, correct: false },
    };
    render(
      <QuizQuestionList
        questions={QUESTIONS}
        answers={answers}
        onAnswer={() => {}}
      />,
    );

    const correct = buttonFor(0, 0); // correct answer → lime
    const chosenWrong = buttonFor(0, 1); // chosen but wrong → pink
    const other = buttonFor(0, 2); // neither → dimmed

    expect(correct.className).toContain("bg-success");
    expect(correct.className).toContain("text-white");
    expect(correct.className).toContain("font-semibold");

    expect(chosenWrong.className).toContain("bg-danger");
    expect(chosenWrong.className).toContain("text-white");
    expect(chosenWrong.className).toContain("font-semibold");

    expect(other.className).toContain("text-on-surface-muted");
    expect(other.className).toContain("opacity-60");

    // Answered options are disabled and the explanation is revealed.
    expect(correct).toBeDisabled();
    expect(chosenWrong).toBeDisabled();
    expect(screen.getByText("Two plus two is four.")).toBeInTheDocument();
  });

  it("highlights only the correct option when answered correctly", () => {
    const answers: Record<string, AnswerState> = {
      q1: { chosen: 0, correct: true },
    };
    render(
      <QuizQuestionList
        questions={QUESTIONS}
        answers={answers}
        onAnswer={() => {}}
      />,
    );

    const correct = buttonFor(0, 0);
    const otherA = buttonFor(0, 1);
    const otherB = buttonFor(0, 2);

    expect(correct.className).toContain("bg-success");
    // Non-correct options are dimmed, never danger-colored, when the choice was right.
    expect(otherA.className).toContain("text-on-surface-muted");
    expect(otherB.className).toContain("text-on-surface-muted");
    expect(otherA.className).not.toContain("bg-danger");
    expect(otherB.className).not.toContain("bg-danger");
  });

  it("invokes onAnswer with the question and chosen option index", () => {
    const onAnswer = vi.fn();
    render(
      <QuizQuestionList
        questions={QUESTIONS}
        answers={{}}
        onAnswer={onAnswer}
      />,
    );

    fireEvent.click(buttonFor(1, 2));
    expect(onAnswer).toHaveBeenCalledTimes(1);
    expect(onAnswer).toHaveBeenCalledWith(QUESTIONS[1], 2);
  });

  it("renders identical markup for a graded quiz (snapshot)", () => {
    const answers: Record<string, AnswerState> = {
      q1: { chosen: 1, correct: false },
      q2: { chosen: 0, correct: true },
    };
    const { container } = render(
      <QuizQuestionList
        questions={QUESTIONS}
        answers={answers}
        onAnswer={() => {}}
      />,
    );
    expect(container).toMatchSnapshot();
  });
});
