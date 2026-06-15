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

  // Brutalist option states: thick borders, flat fills, no rounding.
  function optionClass(q: QuizQuestion, i: number): string {
    const a = answers[q.id];
    if (!a) return "border-ink bg-white text-ink hover:bg-accent-yellow hover:-translate-y-[1px] hover:shadow-brutal-sm";
    if (i === q.correct_index) return "border-ink bg-accent-lime text-ink font-bold";
    if (i === a.chosen) return "border-ink bg-accent-pink text-white font-bold";
    return "border-ink/30 bg-white/40 text-ink/40";
  }

  return (
    <div className="absolute inset-0 z-40 flex">
      <div className="flex-1 bg-ink/50" onClick={onClose} />
      <div
        className="w-[85%] max-w-sm h-full bg-paper border-l-[3px] border-ink overflow-y-auto"
        style={{ scrollbarWidth: "none" }}
      >
        <div className="sticky top-0 bg-paper px-4 py-3 border-b-[3px] border-ink">
          <div className="flex items-center justify-between gap-2">
            <p className="text-ink font-black text-base">Your plan</p>
            <div className="flex items-center gap-2 text-xs">
              {streak > 1 && (
                <span className="brutal bg-accent-orange text-ink font-bold px-2 py-0.5">Streak {streak}</span>
              )}
              <span className="brutal bg-accent-yellow text-ink font-bold px-2 py-0.5 tabular-nums">
                Score {totalScore}
              </span>
              <button
                onClick={onClose}
                className="brutal-btn bg-white text-ink font-black w-7 h-7 flex items-center justify-center shadow-brutal-sm"
                aria-label="Close plan"
              >
                X
              </button>
            </div>
          </div>
          {topics.length > 0 && (
            <p className="text-ink/60 text-xs font-bold mt-2">
              Topics mastered: {masteredCount} / {topics.length}
            </p>
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
                className={`brutal bg-white ${isActive ? "shadow-brutal" : ""}`}
              >
                <div className={`flex items-center gap-2 px-3 py-2.5 ${isActive ? "bg-accent-cyan" : "bg-white"}`}>
                  <span className="bg-ink text-paper w-5 h-5 flex items-center justify-center text-xs font-black shrink-0">
                    {i + 1}
                  </span>
                  <button className="flex-1 min-w-0 text-left" onClick={() => onJump(topic.slug, null)}>
                    <p className="text-sm font-bold truncate text-ink">{topic.name}</p>
                  </button>
                  {qs.length > 0 && (
                    <span className="text-ink/60 text-xs font-bold tabular-nums shrink-0">{correctCount}/{qs.length}</span>
                  )}
                  {mastery?.topics[topic.slug]?.mastered && (
                    <span className="brutal bg-accent-lime text-ink text-[10px] font-black px-1.5 py-0.5 shrink-0">MASTERED</span>
                  )}
                  <button
                    onClick={() => toggle(topic.slug)}
                    className="text-ink font-black text-sm px-1.5 py-1"
                    aria-label="Show sections"
                  >
                    {expanded === topic.slug ? "^" : "v"}
                  </button>
                </div>

                {expanded === topic.slug && (
                  <div className="border-t-[3px] border-ink bg-paper">
                    {/* Notes */}
                    <p className="px-4 pt-2.5 pb-1 text-ink/50 text-[10px] font-black tracking-wide">NOTES</p>
                    {loading === topic.slug ? (
                      <div className="px-4 py-2 text-ink/50 text-xs font-medium">Loading...</div>
                    ) : sections[topic.slug]?.length ? (
                      <div className="divide-y-[3px] divide-ink border-t-[3px] border-ink">
                        {sections[topic.slug].map((sec) => (
                          <button
                            key={sec.section_index}
                            onClick={() => onJump(topic.slug, sec.section_index)}
                            className="w-full flex items-center gap-2 text-left px-4 py-2 bg-white hover:bg-accent-yellow transition-colors"
                          >
                            <span className="text-ink/50 text-xs font-bold w-3 shrink-0">{sec.section_index + 1}</span>
                            <p className="text-ink text-xs font-bold flex-1 min-w-0 truncate">{sec.title}</p>
                            <span className="text-ink font-black text-xs shrink-0">{">"}</span>
                          </button>
                        ))}
                      </div>
                    ) : (
                      <div className="px-4 py-2 text-ink/50 text-xs font-medium">No sections yet.</div>
                    )}

                    {/* Quiz */}
                    {loading !== topic.slug && (
                      <>
                        <p className="px-4 pt-3 pb-1 text-ink/50 text-[10px] font-black tracking-wide">TEST YOURSELF</p>
                        {qs.length === 0 ? (
                          <div className="px-4 pb-3 text-ink/50 text-xs font-medium">Quiz coming soon.</div>
                        ) : (
                          <div className="px-4 pb-3 space-y-3">
                            {qs.map((q) => {
                              const a = answers[q.id];
                              return (
                                <div key={q.id} className="space-y-1.5">
                                  <p className="text-ink text-xs font-bold">{q.question}</p>
                                  {q.options.map((opt, oi) => (
                                    <button
                                      key={oi}
                                      disabled={!!a}
                                      onClick={() => answer(q, oi)}
                                      className={`w-full text-left text-xs px-3 py-2 border-2 rounded-none font-medium transition-all duration-75 ${optionClass(q, oi)}`}
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
