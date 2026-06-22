"use client";

import { useEffect, useRef, useState } from "react";
import {
  getPlacementQuestions,
  submitPlacement,
  type PlacementAnswer,
  type QuizQuestion,
  type Topic,
} from "@/lib/api";
import { QuizQuestionList, type AnswerState } from "@/components/SoftCheckpointCard";

/**
 * Short, optional pre-feed placement screen (Phase 2, Req 2.1, 4.2).
 *
 * Presents ~5-8 diagnostic questions from
 * `GET /api/placement/{session_id}/questions`, submits the answers to
 * `POST /api/placement/{session_id}`, and seeds the leveled feed with the
 * returned entry point (mapped to a `start_topic` slug).
 *
 * Optional and non-blocking by construction:
 * - When no questions come back (none cached yet, or any failure), the screen is
 *   skipped entirely and the feed is entered exactly as before (no regression).
 * - A prominent "Skip" submits `{ skip: true }`, which defers placement to the
 *   learner's `grade_level`, then enters the feed.
 * - Any submit failure falls back to entering the feed unseeded.
 *
 * Reuses the shared `QuizQuestionList` MCQ UI and neo-brutalist styling from the
 * soft checkpoint card so the diagnostic looks like the rest of the curriculum.
 */
export default function PlacementScreen({
  sessionId,
  token,
  topics,
  query,
  onEnterFeed,
}: {
  sessionId: string;
  token: string;
  /** The session's planned topics, used to map an `entry_index` to a slug. */
  topics: Topic[];
  /** The learner's original query, shown as context above the diagnostic. */
  query?: string;
  /**
   * Enter the feed. `startTopicSlug` seeds the scroll at that topic (from the
   * placement `entry_index`); `null` enters the feed from the start (as today).
   */
  onEnterFeed: (startTopicSlug: string | null) => void;
}) {
  const [questions, setQuestions] = useState<QuizQuestion[]>([]);
  const [loading, setLoading] = useState(true);
  // True while the backend is still generating the diagnostic on a cold start
  // (no `pre` questions cached yet). Shows a "building" state with Skip instead
  // of silently entering the feed, so first-run placement actually appears.
  const [building, setBuilding] = useState(false);
  const [answers, setAnswers] = useState<Record<string, AnswerState>>({});
  const [submitting, setSubmitting] = useState(false);
  // Guards onEnterFeed against being called more than once (e.g. an empty-result
  // auto-enter racing a manual submit).
  const enteredRef = useRef(false);

  function enterFeed(startTopicSlug: string | null) {
    if (enteredRef.current) return;
    enteredRef.current = true;
    onEnterFeed(startTopicSlug);
  }

  // Map a placement entry_index (an index into the prerequisite-ordered path) to
  // a topic slug for the feed's `start_topic` seeding. Out-of-range / null ->
  // null (enter from the start).
  function slugForEntry(entryIndex: number | null): string | null {
    if (entryIndex == null || entryIndex < 0 || entryIndex >= topics.length) return null;
    return topics[entryIndex]?.slug ?? null;
  }

  // Fetch the diagnostic on mount, polling while the backend generates it on a
  // cold start. The first call to GET /api/placement/{session}/questions kicks
  // off background generation and returns [] until questions are cached; we poll
  // a bounded number of times (showing a "building" state) and only fall through
  // to entering the feed once questions arrive, the learner skips, or the budget
  // is exhausted. This makes first-run placement appear instead of silently
  // skipping, while never blocking the request path or the scroll.
  useEffect(() => {
    if (!token) {
      enterFeed(null);
      return;
    }
    let cancelled = false;
    let attempts = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const POLL_MS = 3000;
    const MAX_ATTEMPTS = 8; // ~24s budget; first topic's questions usually land by ~2-3 polls

    async function load() {
      let qs: QuizQuestion[] = [];
      try {
        qs = await getPlacementQuestions(sessionId, token);
      } catch {
        qs = [];
      }
      if (cancelled) return;
      if (qs.length > 0) {
        setQuestions(qs);
        setBuilding(false);
        setLoading(false);
        return;
      }
      attempts += 1;
      if (attempts >= MAX_ATTEMPTS) {
        // Nothing generated in time -- enter the feed unseeded (no regression).
        enterFeed(null);
        return;
      }
      // Still generating: show the building state and poll again.
      setBuilding(true);
      timer = setTimeout(load, POLL_MS);
    }

    load();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, token]);

  function onAnswer(q: QuizQuestion, choiceIndex: number) {
    if (answers[q.id]) return; // lock each question after the first answer
    setAnswers((p) => ({
      ...p,
      [q.id]: { chosen: choiceIndex, correct: choiceIndex === q.correct_index },
    }));
  }

  async function handleStart() {
    if (submitting) return;
    setSubmitting(true);
    const graded: PlacementAnswer[] = Object.entries(answers).map(([qid, a]) => ({
      question_id: qid,
      chosen_index: a.chosen,
    }));
    const result = await submitPlacement(sessionId, { answers: graded }, token);
    enterFeed(slugForEntry(result?.entry_index ?? null));
  }

  async function handleSkip() {
    if (submitting) return;
    setSubmitting(true);
    const result = await submitPlacement(sessionId, { skip: true }, token);
    enterFeed(slugForEntry(result?.entry_index ?? null));
  }

  // While the diagnostic loads / is still being generated, show a building state
  // with Skip always available (never a dead-end spinner).
  if (loading) {
    return (
      <div className="fixed inset-0 bg-paper text-ink flex flex-col items-center justify-center gap-5 px-6">
        <div className="w-12 h-12 border-[3px] border-ink border-t-accent-pink rounded-full animate-spin" />
        {building ? (
          <>
            <div className="text-center space-y-1">
              <p className="font-extrabold">Building your quick placement...</p>
              <p className="text-ink/60 text-sm font-medium">
                A few questions to start you at the right level.
              </p>
            </div>
            <button
              onClick={() => enterFeed(null)}
              className="brutal-btn bg-white text-ink px-6 py-3 text-sm"
            >
              Skip and start watching
            </button>
          </>
        ) : null}
      </div>
    );
  }

  const answeredCount = Object.keys(answers).length;

  return (
    <main className="fixed inset-0 bg-paper text-ink overflow-y-auto">
      <div className="min-h-full flex flex-col items-center px-4 py-10">
        <div className="w-full max-w-xl space-y-6">
          <div className="space-y-2">
            <div className="inline-block bg-accent-cyan brutal px-2 py-0.5 text-xs font-black uppercase tracking-wide">
              Quick placement
            </div>
            <h1 className="text-3xl font-black leading-tight">
              Let&apos;s find your starting point
            </h1>
            <p className="text-ink/60 text-sm font-medium">
              Answer a few quick questions so we can start you at the right level.
              {" "}This is optional — skip any time.
              {query ? <span className="block mt-1 text-ink/40">For: {query}</span> : null}
            </p>
          </div>

          <div className="brutal-card p-5 space-y-4">
            <QuizQuestionList questions={questions} answers={answers} onAnswer={onAnswer} />
            <div className="flex items-center justify-between gap-2 pt-1">
              <span className="text-ink/50 text-xs font-bold tabular-nums">
                {answeredCount}/{questions.length} answered
              </span>
            </div>
          </div>

          <div className="flex gap-3">
            <button
              onClick={handleSkip}
              disabled={submitting}
              className="brutal-btn flex-1 bg-white text-ink py-3 text-sm disabled:opacity-40"
            >
              Skip
            </button>
            <button
              onClick={handleStart}
              disabled={submitting}
              className="brutal-btn flex-1 bg-accent-yellow text-ink py-3 text-sm disabled:opacity-40"
            >
              {submitting ? "Placing you..." : "Start learning"}
            </button>
          </div>
        </div>
      </div>
    </main>
  );
}
