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
  type ProgressTopic,
} from "@/lib/api";
import {
  QuizQuestionList,
  pointsFor,
  type AnswerState,
} from "@/components/SoftCheckpointCard";

export interface PlanTopic {
  slug: string;
  name: string;
}

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
  progressTopics,
}: {
  open: boolean;
  onClose: () => void;
  topics: PlanTopic[];
  activeSlug: string;
  sessionId: string | null;
  onJump: (slug: string, sectionIndex: number | null) => void;
  // Real per-topic mastery from GET /api/progress, keyed by topic slug (task
  // 19.1). When present it drives the MASTERED / NEXT UP badges; when absent the
  // panel falls back to the quiz-mastery signal so there is no regression.
  progressTopics?: Record<string, ProgressTopic>;
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

  // Light self-heal poll: when a topic is expanded but its quiz is still empty,
  // the server generates one lazily in the background. Re-fetch a few times so
  // the quiz appears without the user having to collapse/re-expand. Stops as
  // soon as questions arrive or after a bounded number of attempts.
  useEffect(() => {
    if (!open || !expanded || !token) return;
    const slug = expanded;
    if (loading === slug) return;
    if ((quizzes[slug]?.length ?? 0) > 0) return;

    let cancelled = false;
    let attempts = 0;
    const MAX_ATTEMPTS = 6;
    const INTERVAL_MS = 5000;

    const timer = setInterval(async () => {
      if (cancelled) return;
      attempts += 1;
      try {
        const qs = await getQuiz(slug, token);
        if (!cancelled && qs.length > 0) {
          setQuizzes((p) => ({ ...p, [slug]: qs }));
          clearInterval(timer);
        }
      } catch {
        /* best-effort; keep trying until the attempt cap */
      }
      if (attempts >= MAX_ATTEMPTS) clearInterval(timer);
    }, INTERVAL_MS);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [open, expanded, token, quizzes, loading]);

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

  // Prefer the real progress map for mastery; fall back to the quiz-mastery
  // signal when progress is unavailable (no regression).
  const isTopicMastered = (slug: string): boolean =>
    progressTopics ? !!progressTopics[slug]?.mastered : !!mastery?.topics[slug]?.mastered;

  const totalScore = (mastery?.total_points ?? 0) + scoreDelta;
  const masteredCount = topics.filter((t) => isTopicMastered(t.slug)).length;

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
                  {isTopicMastered(topic.slug) ? (
                    <span className="brutal bg-accent-lime text-ink text-[10px] font-black px-1.5 py-0.5 shrink-0">MASTERED</span>
                  ) : progressTopics?.[topic.slug]?.unlock === "recommended" ? (
                    <span className="brutal bg-accent-orange text-ink text-[10px] font-black px-1.5 py-0.5 shrink-0">NEXT UP</span>
                  ) : null}
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
                          <div className="px-4 pb-3 text-ink/50 text-xs font-medium">Building your quiz...</div>
                        ) : (
                          <div className="px-4 pb-3">
                            <QuizQuestionList questions={qs} answers={answers} onAnswer={answer} />
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
