"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { getUserHistory, getUserProfile, type LearningPathSummary } from "@/lib/api";
import { hasSeenIntro } from "@/lib/intro";
import LegalFooter from "@/components/LegalFooter";

const SUGGESTIONS = [
  "I want to learn about binary trees",
  "Teach me cell biology from scratch",
  "Teach me about World War 2",
  "I need to understand calculus derivatives",
];

// Rotating accent colors for the suggestion blocks.
const CHIP_COLORS = ["bg-accent-yellow", "bg-accent-cyan", "bg-accent-lime", "bg-accent-pink"];

export default function Home() {
  const router = useRouter();
  const { user, session, loading, signOut, isGuest, isAuthenticated } = useAuth();
  const [query, setQuery] = useState("");
  const [history, setHistory] = useState<LearningPathSummary[]>([]);

  useEffect(() => {
    if (!user || !session) return;
    // The intro/demo carousel is the very first thing a new visitor sees —
    // gate on it before anything else (history, onboarding) so it can't be
    // skipped past by a faster-resolving redirect.
    if (!hasSeenIntro()) {
      router.replace("/welcome");
      return;
    }
    getUserHistory(user.id, session.access_token).then(setHistory).catch(() => {});
    if (isAuthenticated) {
      getUserProfile(user.id, session.access_token).then((p) => {
        if (!p.onboarding_complete) router.replace("/onboarding");
      }).catch(() => {});
    }
  }, [user, session, isAuthenticated]);

  // Entry point into the Adaptive Learning Game. The launcher no longer plans a
  // path or renders a placement/plan overview here — it routes the learner into
  // the `/play` game route, carrying the typed topic so the game can start the
  // probe on it.
  function startQuest(q: string) {
    const trimmed = q.trim();
    if (!trimmed) return;
    router.push(`/play?topic=${encodeURIComponent(trimmed)}`);
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
        <LegalFooter />
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
            <div className="flex items-center gap-3">
              <button
                onClick={() => router.push("/welcome")}
                className="text-ink/50 hover:text-ink text-xs font-bold transition"
              >
                How it works
              </button>
              <button
                onClick={() => router.push("/login")}
                className="brutal-btn bg-accent-cyan text-ink text-sm px-3 py-2"
              >
                Save progress
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-3">
              <button
                onClick={() => router.push("/welcome")}
                className="text-ink/50 hover:text-ink text-xs font-bold transition"
              >
                How it works
              </button>
              <button
                onClick={signOut}
                className="brutal-btn bg-white text-ink text-sm px-3 py-2"
              >
                Sign out
              </button>
            </div>
          )}
        </div>

        <div className="space-y-3">
          <p className="text-3xl font-black leading-tight">What do you want to learn today?</p>
          <div className="flex gap-3">
            <input
              className="brutal flex-1 bg-white px-4 py-3 text-ink placeholder-ink/40 focus:outline-none focus:shadow-brutal font-medium"
              placeholder="e.g. hashmaps and dynamic programming"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && startQuest(query)}
            />
            <button
              onClick={() => startQuest(query)}
              disabled={!query.trim()}
              className="brutal-btn bg-accent-yellow text-ink px-6 py-3 disabled:opacity-40"
            >
              Go
            </button>
          </div>
        </div>

        <div className="space-y-2">
          <p className="text-ink/60 text-xs font-bold uppercase tracking-wide">Try</p>
          <div className="grid grid-cols-1 gap-2">
            {SUGGESTIONS.map((s, i) => (
              <button
                key={s}
                onClick={() => startQuest(s)}
                className={`brutal-btn ${CHIP_COLORS[i % CHIP_COLORS.length]} text-ink text-left text-sm px-3 py-2.5`}
              >
                {s}
              </button>
            ))}
          </div>
        </div>

        {/* Secondary entry: just-browse the Discover feed. Kept visually
            quiet so it never competes with the primary search action. */}
        <div className="flex items-center gap-3 pt-1">
          <div className="flex-1 h-[3px] bg-ink/15" />
          <span className="text-ink/40 text-xs font-bold uppercase tracking-wide">or</span>
          <div className="flex-1 h-[3px] bg-ink/15" />
        </div>
        <button
          onClick={() => router.push("/discover")}
          className="brutal-btn w-full bg-white text-ink py-3 text-sm flex items-center justify-center gap-2"
        >
          Surprise me
          <span className="font-black">{">"}</span>
        </button>

        {history.length > 0 && (
          <div className="space-y-2">
            <p className="text-ink/60 text-xs font-bold uppercase tracking-wide">Continue where you left off</p>
            <div className="space-y-2">
              {history.map((h) => (
                <button
                  key={h.session_id}
                  onClick={() => router.push(`/feed?session=${h.session_id}`)}
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
      </div>
      <LegalFooter />
    </main>
  );
}
