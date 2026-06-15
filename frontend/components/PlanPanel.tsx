"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth-context";
import {
  getTopicSections,
  getQuiz,
  getQuizMastery,
  recordQuizAnswer,
  type TopicSection,
  type QuizQuestion,
  type QuizMastery,
} from "@/lib/api";

export interface PlanTopic {
  slug: string;
  name: string;
}

// Mirror the server's points_for so the optimistic score matches what's stored.
const POINTS_PER_CORRECT = 10;
const STREAK_BONUS_PER = 5;
const MAX_STREAK_BONUS = 40;
function pointsFor(streak: number): number {
  return POINTS_PER_CORRECT + Math.min(Math.max(0, streak) * STREAK_BONUS_PER, MAX_STREAK_BONUS);
}

type AnswerState = { chosen: number; correct: boolean };

/**
 * Study panel: the learning plan (notes) plus an optional, gamified MCQ quiz per
 * topic, with a live score and streak. Lives in the feed as a side overlay;
 * never interrupts playback. Grading is instant client-side; recording is
 * fire-and-forget.
 */
export default function PlanPanel({
  open,
  onClose,
  topics,
  activeSlug,
  sessionId,
  onJump,
}: {
  open: boolean;
  onClose: () => void;
  topics: PlanTopic[];
  activeSlug: string;
  sessionId: string | null;
  onJump: (slug: string, sectionIndex: number | null) => void;
}) {
  const { user, session } = useAuth();
  const token = session?.access_token ?? "";

  const [expanded, setExpanded] = useState<string | null>(null);
  const [sections, setSections] = useState<Record<string, TopicSection[]>>({});
  const [quizzes, setQuizzes] = useState<Record<string, QuizQuestion[]>>({});
  const [loading, setLoading] = useState<string | null>(null);

  const [mastery, setMastery] = useState<QuizMastery | null>(null);
  const [answers, setAnswers] = useState<Record<string, AnswerState>>({});
  const [streak, setStreak] = useState(0);
  const [scoreDelta, setScoreDelta] = useState(0); // optimistic points earned this session

  // Load mastery (score + mastered topics) when the panel opens.
  useEffect(() => {
    if (!open || !user || !token) return;
    getQuizMastery(user.id, token).then(setMastery).catch(() => {});
  }, [open, user, token]);

  async function toggle(slug: string) {
    if (expanded === slug) {
      setExpanded(null);
      return;
    }
    setExpanded(slug);
    if (sections[slug] === undefined && token) {
      setLoading(slug);
      try {
        const [secs, qs] = await Promise.all([
          getTopicSections(slug, token),
          getQuiz(slug, token),
        ]);
        setSections((p) => ({ ...p, [slug]: secs }));
        setQuizzes((p) => ({ ...p, [slug]: qs }));
      } catch {
        setSections((p) => ({ ...p, [slug]: [] }));
      }
      setLoading(null);
    }
  }

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
  }

  if (!open) return null;

  const totalScore = (mastery?.total_points ?? 0) + scoreDelta;
  const masteredCount = mastery
    ? topics.filter((t) => mastery.topics[t.slug]?.mastered).length
    : 0;

  function optionClass(q: QuizQuestion, i: number): string {
    const a = answers[q.id];
    if (!a) return "border-zinc-700 text-zinc-200 hover:bg-zinc-800/60";
    if (i === q.correct_index) return "border-green-500 text-green-300 bg-green-500/10";
    if (i === a.chosen) return "border-red-500 text-red-300 bg-red-500/10";
    return "border-zinc-800 text-zinc-500";
  }

  return (
    <div className="absolute inset-0 z-40 flex">
      <div className="flex-1 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div
        className="w-[85%] max-w-sm h-full bg-zinc-950 border-l border-zinc-800 overflow-y-auto"
        style={{ scrollbarWidth: "none" }}
      >
        <div className="sticky top-0 bg-zinc-950/95 backdrop-blur px-4 py-3 border-b border-zinc-800">
          <div className="flex items-center justify-between">
            <p className="text-white font-semibold text-sm">Your plan</p>
            <div className="flex items-center gap-3 text-xs">
              {streak > 1 && <span className="text-amber-400 font-medium">Streak {streak}</span>}
              <span className="text-zinc-300 tabular-nums">Score {totalScore}</span>
              <button onClick={onClose} className="text-zinc-500 hover:text-white leading-none" aria-label="Close plan">
                X
              </button>
            </div>
          </div>
          {topics.length > 0 && (
            <p className="text-zinc-500 text-xs mt-1">Topics mastered: {masteredCount} / {topics.length}</p>
          )}
        </div>

        <div className="p-3 space-y-2">
          {topics.map((topic, i) => {
            const isActive = topic.slug === activeSlug;
            const qs = quizzes[topic.slug] ?? [];
            const correctCount = qs.filter((q) => answers[q.id]?.correct).length;
            return (
              <div
                key={topic.slug}
                className={`rounded-xl overflow-hidden border ${isActive ? "border-white/40" : "border-zinc-800"}`}
              >
                <div className="flex items-center gap-2 bg-zinc-900/60 px-3 py-2.5">
                  <span className="text-zinc-600 text-xs w-4 shrink-0">{i + 1}</span>
                  <button className="flex-1 min-w-0 text-left" onClick={() => onJump(topic.slug, null)}>
                    <p className={`text-sm font-medium truncate ${isActive ? "text-white" : "text-zinc-300"}`}>
                      {topic.name}
                    </p>
                  </button>
                  {qs.length > 0 && (
                    <span className="text-zinc-500 text-xs tabular-nums shrink-0">{correctCount}/{qs.length}</span>
                  )}
                  {mastery?.topics[topic.slug]?.mastered && (
                    <span className="text-green-400 text-[10px] font-semibold shrink-0">MASTERED</span>
                  )}
                  <button
                    onClick={() => toggle(topic.slug)}
                    className="text-zinc-500 hover:text-white text-xs px-1.5 py-1"
                    aria-label="Show sections"
                  >
                    {expanded === topic.slug ? "^" : "v"}
                  </button>
                </div>

                {expanded === topic.slug && (
                  <div className="bg-zinc-900/40">
                    {/* Notes */}
                    <p className="px-4 pt-2.5 pb-1 text-zinc-600 text-[10px] font-semibold tracking-wide">NOTES</p>
                    {loading === topic.slug ? (
                      <div className="px-4 py-2 text-zinc-600 text-xs">Loading...</div>
                    ) : sections[topic.slug]?.length ? (
                      <div className="divide-y divide-zinc-800/60">
                        {sections[topic.slug].map((sec) => (
                          <button
                            key={sec.section_index}
                            onClick={() => onJump(topic.slug, sec.section_index)}
                            className="w-full flex items-center gap-2 text-left px-4 py-2 hover:bg-zinc-800/60 transition"
                          >
                            <span className="text-zinc-600 text-xs w-3 shrink-0">{sec.section_index + 1}</span>
                            <p className="text-zinc-400 text-xs flex-1 min-w-0 truncate">{sec.title}</p>
                            <span className="text-zinc-600 text-xs shrink-0">{">"}</span>
                          </button>
                        ))}
                      </div>
                    ) : (
                      <div className="px-4 py-2 text-zinc-600 text-xs">No sections yet.</div>
                    )}

                    {/* Quiz */}
                    {loading !== topic.slug && (
                      <>
                        <p className="px-4 pt-3 pb-1 text-zinc-600 text-[10px] font-semibold tracking-wide">TEST YOURSELF</p>
                        {qs.length === 0 ? (
                          <div className="px-4 pb-3 text-zinc-600 text-xs">Quiz coming soon.</div>
                        ) : (
                          <div className="px-4 pb-3 space-y-3">
                            {qs.map((q) => {
                              const a = answers[q.id];
                              return (
                                <div key={q.id} className="space-y-1.5">
                                  <p className="text-zinc-300 text-xs font-medium">{q.question}</p>
                                  {q.options.map((opt, oi) => (
                                    <button
                                      key={oi}
                                      disabled={!!a}
                                      onClick={() => answer(q, oi)}
                                      className={`w-full text-left text-xs px-3 py-2 rounded-lg border transition ${optionClass(q, oi)}`}
                                    >
                                      {opt}
                                    </button>
                                  ))}
                                  {a && <p className="text-zinc-500 text-[11px] pt-0.5">{q.explanation}</p>}
                                </div>
                              );
                            })}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
