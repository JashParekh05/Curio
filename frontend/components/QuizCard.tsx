"use client";

import { type QuizQuestion } from "@/lib/api";

// Mirror the server's points_for so the optimistic score matches what's stored.
// Shared with PlanPanel and SoftCheckpointCard so the "Test Yourself" quiz and
// the inline soft checkpoint card award points identically.
export const POINTS_PER_CORRECT = 10;
export const STREAK_BONUS_PER = 5;
export const MAX_STREAK_BONUS = 40;
export function pointsFor(streak: number): number {
  return (
    POINTS_PER_CORRECT +
    Math.min(Math.max(0, streak) * STREAK_BONUS_PER, MAX_STREAK_BONUS)
  );
}

export type AnswerState = { chosen: number; correct: boolean };

// Brutalist option states: thick borders, flat fills, no rounding. Shared by
// the PlanPanel quiz and the inline soft checkpoint card so both look identical.
function optionClass(
  q: QuizQuestion,
  i: number,
  answers: Record<string, AnswerState>,
): string {
  const a = answers[q.id];
  if (!a)
    return "border-ink bg-white text-ink hover:bg-accent-yellow hover:-translate-y-[1px] hover:shadow-brutal-sm";
  if (i === q.correct_index) return "border-ink bg-accent-lime text-ink font-bold";
  if (i === a.chosen) return "border-ink bg-accent-pink text-white font-bold";
  return "border-ink/30 bg-white/40 text-ink/40";
}

/**
 * Presentational MCQ list reused by both the PlanPanel "Test Yourself" quiz and
 * the inline soft checkpoint card. Grading is instant and client-side; the
 * parent owns the answer map and the award callback so scoring/streak behavior
 * stays in one place.
 */
export function QuizQuestionList({
  questions,
  answers,
  onAnswer,
}: {
  questions: QuizQuestion[];
  answers: Record<string, AnswerState>;
  onAnswer: (q: QuizQuestion, choiceIndex: number) => void;
}) {
  return (
    <div className="space-y-4">
      {questions.map((q, qi) => {
        const a = answers[q.id];
        return (
          <div key={q.id} className="brutal-card p-4 space-y-3">
            {/* Flashcard prompt with a question-number tab for clear hierarchy. */}
            <div className="flex items-start gap-2">
              <span className="brutal bg-accent-purple text-white text-[10px] font-black leading-none px-1.5 py-1 shrink-0">
                Q{qi + 1}
              </span>
              <p className="text-ink text-sm font-bold leading-snug">{q.question}</p>
            </div>
            <div className="space-y-2.5">
              {q.options.map((opt, oi) => (
                <button
                  key={oi}
                  disabled={!!a}
                  onClick={() => onAnswer(q, oi)}
                  className={`w-full text-left text-xs leading-relaxed px-3.5 py-3 border-2 rounded-none font-medium transition-all duration-75 ${optionClass(q, oi, answers)}`}
                >
                  {opt}
                </button>
              ))}
            </div>
            {a && (
              <p className="brutal bg-paper text-ink text-[11px] font-medium leading-relaxed px-2.5 py-2">
                {q.explanation}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}
