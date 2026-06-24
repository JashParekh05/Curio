"use client";

import { useEffect, useState } from "react";
import {
  getQuiz,
  recordQuizAnswer,
  type QuizQuestion,
} from "@/lib/api";
import {
  QuizQuestionList,
  pointsFor,
  POINTS_PER_CORRECT,
  STREAK_BONUS_PER,
  MAX_STREAK_BONUS,
  type AnswerState,
} from "@/components/QuizCard";

// Re-export the shared quiz primitives (moved to QuizCard) so existing
// consumers importing them from here keep working unchanged.
export {
  QuizQuestionList,
  pointsFor,
  POINTS_PER_CORRECT,
  STREAK_BONUS_PER,
  MAX_STREAK_BONUS,
  type AnswerState,
};

/** A soft checkpoint stage: a quick `check` between beats, or a `post` at the
 * topic boundary. Both are always skippable. */
export type CheckpointStage = "check" | "post";

const STAGE_LABEL: Record<CheckpointStage, string> = {
  check: "QUICK CHECK",
  post: "TOPIC CHECKPOINT",
};

/**
 * Inline soft checkpoint card (Friendly Pop). Reuses the "Test Yourself" quiz UI
 * for a `check` (between beats) or `post` (topic boundary) checkpoint, pulling
 * questions by `(topic_slug, stage, section_index)` from the existing quiz
 * endpoint. Soft by construction: a "Skip" affordance is always visible and
 * skipping never blocks — the parent keeps the scroll advancing regardless.
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
    <div className="bg-surface rounded-card border border-outline shadow-elev-2 overflow-hidden">
      <div className="flex items-center justify-between gap-2 px-4 py-3 bg-surface-alt border-b border-outline">
        <div className="min-w-0">
          <p className="text-on-surface-muted text-[10px] font-bold tracking-wide uppercase">
            {STAGE_LABEL[stage]}
          </p>
          {topicName && (
            <p className="text-on-surface text-sm font-display font-bold truncate">{topicName}</p>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {streak > 1 && (
            <span className="rounded-pill bg-warning text-on-accent font-semibold text-xs px-2 py-0.5">
              Streak {streak}
            </span>
          )}
          {scoreDelta > 0 && (
            <span className="rounded-pill bg-success text-white font-semibold text-xs px-2 py-0.5 tabular-nums">
              +{scoreDelta}
            </span>
          )}
          {/* Soft invariant: skipping is always available and never blocks. */}
          <button
            onClick={onSkip}
            className="rounded-pill bg-surface text-on-surface border border-outline font-semibold text-xs px-3 h-7 flex items-center justify-center transition hover:brightness-95"
            aria-label="Skip checkpoint"
          >
            Skip
          </button>
        </div>
      </div>

      <div className="px-4 py-3">
        {loading ? (
          <p className="text-on-surface-muted text-xs font-medium">Building your quiz...</p>
        ) : questions.length === 0 ? (
          <div className="space-y-2">
            <p className="text-on-surface-muted text-xs font-medium">
              No checkpoint questions yet.
            </p>
            <button
              onClick={onSkip}
              className="rounded-pill bg-primary text-on-primary font-semibold text-xs px-4 h-8 shadow-elev-1 transition hover:brightness-[1.05]"
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
              <span className="text-on-surface-muted text-xs font-semibold tabular-nums">
                {correctCount}/{questions.length} correct
              </span>
              <button
                onClick={onSkip}
                className="rounded-pill bg-primary text-on-primary font-semibold text-xs px-4 h-8 shadow-elev-1 transition hover:brightness-[1.05]"
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
