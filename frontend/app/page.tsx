"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { createLearningPath, getUserHistory, getUserProfile, getTopicSections, type LearningPath, type LearningPathSummary, type TopicSection } from "@/lib/api";

const SUGGESTIONS = [
  "I want to learn hashmaps and binary trees",
  "Teach me cell biology from scratch",
  "Explain machine learning basics",
  "I need to understand calculus derivatives",
];

// Rotating accent colors for the suggestion blocks.
const CHIP_COLORS = ["bg-accent-yellow", "bg-accent-cyan", "bg-accent-lime", "bg-accent-pink"];

export default function Home() {
  const router = useRouter();
  const { user, session, loading, signOut, isGuest, isAuthenticated } = useAuth();
  const [query, setQuery] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [path, setPath] = useState<LearningPath | null>(null);
  const [history, setHistory] = useState<LearningPathSummary[]>([]);
  const [expandedTopic, setExpandedTopic] = useState<string | null>(null);
  const [sectionsByTopic, setSectionsByTopic] = useState<Record<string, TopicSection[]>>({});
  const [loadingSections, setLoadingSections] = useState<string | null>(null);

  useEffect(() => {
    if (!user || !session) return;
    getUserHistory(user.id, session.access_token).then(setHistory).catch(() => {});
    if (isAuthenticated) {
      getUserProfile(user.id, session.access_token).then((p) => {
        if (!p.onboarding_complete) router.replace("/onboarding");
      }).catch(() => {});
    }
  }, [user, session, isAuthenticated]);

  async function toggleSections(slug: string) {
    if (expandedTopic === slug) {
      setExpandedTopic(null);
      return;
    }
    setExpandedTopic(slug);
    if (!sectionsByTopic[slug] && session) {
      setLoadingSections(slug);
      try {
        const sections = await getTopicSections(slug, session.access_token);
        setSectionsByTopic((prev) => ({ ...prev, [slug]: sections }));
      } catch {}
      setLoadingSections(null);
    }
  }

  async function handleSubmit(q: string) {
    const trimmed = q.trim();
    if (!trimmed || !user || !session || submitting) return;
    // Route the learner to THEIR topic feed (not Discover) — the path is
    // planned server-side first, then the feed streams in matching clips.
    setSubmitting(true);
    setError("");
    try {
      const result = await createLearningPath(trimmed, user.id, session.access_token);
      router.push(`/feed?session=${result.session_id}`);
    } catch {
      setError("Couldn't build your learning path. Please try again.");
      setSubmitting(false);
    }
  }

  if (loading) return null;

  if (!user) {
    return (
      <main className="min-h-screen bg-paper text-ink flex flex-col items-center justify-center px-4 gap-6">
        <h1 className="text-6xl font-black tracking-tight">
          Curio<span className="text-accent-pink">.</span>
        </h1>
        <p className="text-ink/70 text-sm text-center font-medium">Sign in to start learning.</p>
        <button
          onClick={() => router.push("/login")}
          className="brutal-btn bg-accent-yellow text-ink px-8 py-3 text-lg"
        >
          Sign in
        </button>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-paper text-ink flex flex-col items-center px-4 py-10">
      <div className="w-full max-w-xl space-y-7">
        {/* Header */}
        <div className="flex items-end justify-between">
          <div>
            <h1 className="text-5xl font-black tracking-tight leading-none">
              Curio<span className="text-accent-pink">.</span>
            </h1>
            <div className="mt-2 inline-block bg-accent-lime brutal px-2 py-0.5 text-xs font-bold">
              {isGuest ? "GUEST" : user.email}
            </div>
          </div>
          {isGuest ? (
            <button
              onClick={() => router.push("/login")}
              className="brutal-btn bg-accent-cyan text-ink text-sm px-3 py-2"
            >
              Save progress
            </button>
          ) : (
            <button
              onClick={signOut}
              className="brutal-btn bg-white text-ink text-sm px-3 py-2"
            >
              Sign out
            </button>
          )}
        </div>

        {/* Mode tabs */}
        <div className="flex gap-3">
          <button
            onClick={() => router.push("/discover")}
            className="brutal-btn flex-1 bg-white text-ink py-3 text-sm"
          >
            Discover
          </button>
          <div className="brutal flex-1 bg-accent-purple text-white font-extrabold py-3 text-sm text-center shadow-brutal">
            Learn
          </div>
        </div>

        {!path ? (
          <>
            {submitting ? (
              <div className="space-y-4">
                <div className="brutal-card p-6 flex flex-col items-center gap-4 text-center">
                  <div className="w-10 h-10 border-[3px] border-ink border-t-accent-pink rounded-full animate-spin" />
                  <div className="space-y-1">
                    <p className="font-extrabold">Building your learning path...</p>
                    <p className="text-ink/60 text-sm line-clamp-1">{query}</p>
                  </div>
                </div>
                <button
                  onClick={() => router.push("/discover")}
                  className="brutal-btn w-full bg-accent-cyan text-ink py-3 text-sm"
                >
                  Browse Discover while you wait
                </button>
              </div>
            ) : (
              <>
                <div className="space-y-3">
                  <p className="text-2xl font-black leading-tight">What do you want to learn today?</p>
                  <div className="flex gap-3">
                    <input
                      className="brutal flex-1 bg-white px-4 py-3 text-ink placeholder-ink/40 focus:outline-none focus:shadow-brutal font-medium"
                      placeholder="e.g. hashmaps and dynamic programming"
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && handleSubmit(query)}
                    />
                    <button
                      onClick={() => handleSubmit(query)}
                      disabled={!query.trim()}
                      className="brutal-btn bg-accent-yellow text-ink px-6 py-3 disabled:opacity-40"
                    >
                      Go
                    </button>
                  </div>
                  {error && (
                    <div className="brutal bg-accent-pink text-white text-sm font-bold px-3 py-2">{error}</div>
                  )}
                </div>

                <div className="space-y-2">
                  <p className="text-ink/60 text-xs font-bold uppercase tracking-wide">Try</p>
                  <div className="grid grid-cols-1 gap-2">
                    {SUGGESTIONS.map((s, i) => (
                      <button
                        key={s}
                        onClick={() => { setQuery(s); handleSubmit(s); }}
                        className={`brutal-btn ${CHIP_COLORS[i % CHIP_COLORS.length]} text-ink text-left text-sm px-3 py-2.5`}
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                </div>
              </>
            )}

            {history.length > 0 && (
              <div className="space-y-2">
                <p className="text-ink/60 text-xs font-bold uppercase tracking-wide">Continue where you left off</p>
                <div className="space-y-2">
                  {history.map((h) => (
                    <button
                      key={h.session_id}
                      onClick={() => setPath({
                        session_id: h.session_id,
                        user_query: h.user_query,
                        topics: h.topic_slugs.map((slug) => ({
                          slug,
                          name: slug.split("-").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" "),
                          difficulty: "beginner" as const,
                          prerequisites: [],
                          rationale: "",
                        })),
                        summary: h.user_query,
                        familiarity_prompt: null,
                        suggested_start_index: 0,
                      })}
                      className="brutal-btn w-full bg-white text-left px-4 py-3 flex items-center justify-between"
                    >
                      <div className="min-w-0">
                        <p className="text-sm font-bold line-clamp-1">{h.user_query}</p>
                        <p className="text-ink/50 text-xs mt-0.5">{h.topic_count} topics</p>
                      </div>
                      <span className="font-black text-lg shrink-0 ml-2">{">"}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </>
        ) : (
          <div className="space-y-5">
            <div className="brutal-card p-5 space-y-4">
              <p className="text-ink text-sm font-medium">{path.summary}</p>
              <div className="space-y-2">
                {path.topics.map((topic, i) => (
                  <div key={topic.slug} className="brutal bg-white">
                    <div className="flex items-center gap-3 px-3 py-3">
                      <span className="bg-accent-yellow brutal w-7 h-7 flex items-center justify-center text-xs font-black shrink-0">{i + 1}</span>
                      <button
                        className="flex-1 min-w-0 text-left"
                        onClick={() => router.push(`/feed?session=${path.session_id}&start_topic=${topic.slug}`)}
                      >
                        <p className="font-bold text-sm">{topic.name}</p>
                        <p className="text-ink/50 text-xs capitalize mt-0.5">{topic.difficulty}</p>
                      </button>
                      <button
                        onClick={() => toggleSections(topic.slug)}
                        className="font-black text-ink px-2 py-1"
                        aria-label="Show sections"
                      >
                        {expandedTopic === topic.slug ? "^" : "v"}
                      </button>
                    </div>

                    {expandedTopic === topic.slug && (
                      <div className="border-t-[3px] border-ink divide-y-[3px] divide-ink">
                        {loadingSections === topic.slug ? (
                          <div className="px-4 py-3 text-ink/50 text-xs font-medium">Loading sections...</div>
                        ) : sectionsByTopic[topic.slug]?.length > 0 ? (
                          sectionsByTopic[topic.slug].map((sec) => (
                            <button
                              key={sec.section_index}
                              onClick={() => router.push(`/feed?session=${path.session_id}&start_topic=${topic.slug}&start_section=${sec.section_index}`)}
                              className="w-full flex items-center gap-3 text-left px-4 py-2.5 bg-paper hover:bg-accent-cyan transition-colors"
                            >
                              <span className="text-ink/50 text-xs font-bold w-4 shrink-0">{sec.section_index + 1}</span>
                              <div className="flex-1 min-w-0">
                                <p className="text-ink text-xs font-bold">{sec.title}</p>
                                <p className="text-ink/50 text-xs mt-0.5 line-clamp-1">{sec.description}</p>
                              </div>
                              <span className="font-black text-xs shrink-0">{">"}</span>
                            </button>
                          ))
                        ) : (
                          <div className="px-4 py-3 text-ink/50 text-xs font-medium">Sections not generated yet — watch a clip to trigger generation.</div>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>

            {path.familiarity_prompt && path.suggested_start_index > 0 ? (
              <div className="brutal-card p-4 space-y-3">
                <p className="text-ink text-sm font-medium">{path.familiarity_prompt}</p>
                <div className="flex gap-3">
                  <button
                    onClick={() => router.push(`/feed?session=${path.session_id}`)}
                    className="brutal-btn flex-1 bg-white text-ink py-3 text-sm"
                  >
                    Start from scratch
                  </button>
                  <button
                    onClick={() => router.push(`/feed?session=${path.session_id}&start=${path.suggested_start_index}`)}
                    className="brutal-btn flex-1 bg-accent-lime text-ink py-3 text-sm"
                  >
                    Jump ahead
                  </button>
                </div>
              </div>
            ) : (
              <button
                onClick={() => router.push(`/feed?session=${path.session_id}`)}
                className="brutal-btn w-full bg-accent-yellow text-ink py-4 text-lg"
              >
                Start Watching
              </button>
            )}

            <button
              onClick={() => { setPath(null); setQuery(""); }}
              className="w-full text-ink/50 hover:text-ink text-sm font-bold py-2 transition"
            >
              Start over
            </button>
          </div>
        )}
      </div>
    </main>
  );
}
