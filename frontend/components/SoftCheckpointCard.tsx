"use client";

import { useEffect, useState } from "react";
import {
  getQuiz,
  recordQuizAnswer,
  type QuizQuestion,
} from "@/lib/api";

// Mirror the server's points_for so the optimistic score matches what's stored.
// Shared with PlanPanel so the "Test Yourself" quiz and the inline soft
// checkpoint card award points identically.
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

/** A soft checkpoint stage: a quick `check` between beats, or a `post` at the
 * topic boundary. Both are always skippable. */
export type CheckpointStage = "check" | "post";

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
    <div className="space-y-3">
      {questions.map((q) => {
        const a = answers[q.id];
        return (
          <div key={q.id} className="space-y-1.5">
            <p className="text-ink text-xs font-bold">{q.question}</p>
            {q.options.map((opt, oi) => (
              <button
                key={oi}
                disabled={!!a}
                onClick={() => onAnswer(q, oi)}
                className={`w-full text-left text-xs px-3 py-2 border-2 rounded-none font-medium transition-all duration-75 ${optionClass(q, oi, answers)}`}
              >
                {opt}
              </button>
            ))}
            {a && (
              <p className="brutal bg-white text-ink text-[11px] font-medium px-2 py-1.5 mt-1">
                {q.explanation}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}

const STAGE_LABEL: Record<CheckpointStage, string> = {
  check: "QUICK CHECK",
  post: "TOPIC CHECKPOINT",
};

/**
 * Inline soft checkpoint card. Reuses the "Test Yourself" quiz UI for a `check`
 * (between beats) or `post` (topic boundary) checkpoint, pulling questions by
 * `(topic_slug, stage, section_index)` from the existing quiz endpoint.
 *
 * Soft by construction: a "Skip" affordance is always visible and skipping or
 * dismissing never blocks anything — the parent (ReelPlayer, task 8.3) keeps the
 * scroll advancing regardless of this card's state. When the backend does not
 * yet support the `stage`/`section_index` query params (added in task 12.5), the
 * endpoint simply returns the topic-wide questions, which this card renders as-is.
 */
export default function SoftCheckpointCard({
  topicSlug,
  topicName,
  stage,
  sectionIndex = null,
  sessionId,
  token,
  onSkip,
  onAnswered,
}: {
  topicSlug: string;
  topicName?: string;
  stage: CheckpointStage;
  sectionIndex?: number | null;
  sessionId: string | null;
  token: string;
  /** Dismiss the card. Always available — checkpoints never block. */
  onSkip: () => void;
  /** Optional: notify the parent of each graded answer (for score rollups). */
  onAnswered?: (correct: boolean) => void;
}) {
  const [questions, setQuestions] = useState<QuizQuestion[]>([]);
  const [loading, setLoading] = useState(true);
  const [answers, setAnswers] = useState<Record<string, AnswerState>>({});
  const [streak, setStreak] = useState(0);
  const [scoreDelta, setScoreDelta] = useState(0);

  // Pull anchored questions for this checkpoint. The stage/section_index params
  // gracefully degrade to topic-wide questions when the backend predates them.
  useEffect(() => {
    if (!token) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    getQuiz(topicSlug, token, { stage, sectionIndex })
      .then((qs) => {
        if (!cancelled) setQuestions(qs);
      })
      .catch(() => {
        if (!cancelled) setQuestions([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [topicSlug, stage, sectionIndex, token]);

  function answer(q: QuizQuestion, choiceIndex: number) {
    if (answers[q.id]) return; // already answered — never re-award
    const correct = choiceIndex === q.correct_index;
    const streakBefore = streak;
    setAnswers((p) => ({ ...p, [q.id]: { chosen: choiceIndex, correct } }));
    if (correct) {
      setScoreDelta((d) => d + pointsFor(streakBefore));
      setStreak((s) => s + 1);
    } else {
      setStreak(0);
    }
    recordQuizAnswer(q.id, choiceIndex, sessionId, streakBefore, token);
    onAnswered?.(correct);
  }

  const answeredCount = Object.keys(answers).length;
  const correctCount = Object.values(answers).filter((a) => a.correct).length;

  return (
    <div className="brutal bg-paper shadow-brutal">
      <div className="flex items-center justify-between gap-2 px-4 py-2.5 bg-accent-cyan border-b-[3px] border-ink">
        <div className="min-w-0">
          <p className="text-ink/60 text-[10px] font-black tracking-wide">
            {STAGE_LABEL[stage]}
          </p>
          {topicName && (
            <p className="text-ink text-sm font-black truncate">{topicName}</p>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {streak > 1 && (
            <span className="brutal bg-accent-orange text-ink font-bold text-xs px-2 py-0.5">
              Streak {streak}
            </span>
          )}
          {scoreDelta > 0 && (
            <span className="brutal bg-accent-yellow text-ink font-bold text-xs px-2 py-0.5 tabular-nums">
              +{scoreDelta}
            </span>
          )}
          {/* Soft invariant: skipping is always available and never blocks. */}
          <button
            onClick={onSkip}
            className="brutal-btn bg-white text-ink font-black text-xs px-2.5 h-7 flex items-center justify-center shadow-brutal-sm"
            aria-label="Skip checkpoint"
          >
            Skip
          </button>
        </div>
      </div>

      <div className="px-4 py-3">
        {loading ? (
          <p className="text-ink/50 text-xs font-medium">Building your quiz...</p>
        ) : questions.length === 0 ? (
          <div className="space-y-2">
            <p className="text-ink/50 text-xs font-medium">
              No checkpoint questions yet.
            </p>
            <button
              onClick={onSkip}
              className="brutal-btn bg-accent-lime text-ink font-black text-xs px-3 h-8 shadow-brutal-sm"
            >
              Keep watching
            </button>
          </div>
        ) : (
          <>
            <QuizQuestionList
              questions={questions}
              answers={answers}
              onAnswer={answer}
            />
            <div className="flex items-center justify-between gap-2 mt-3">
              <span className="text-ink/60 text-xs font-bold tabular-nums">
                {correctCount}/{questions.length} correct
              </span>
              <button
                onClick={onSkip}
                className="brutal-btn bg-accent-lime text-ink font-black text-xs px-3 h-8 shadow-brutal-sm"
              >
                {answeredCount >= questions.length ? "Continue" : "Skip for now"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
