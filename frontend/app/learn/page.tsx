"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import {
  createLearningPath,
  getUserHistory,
  type LearningPath,
  type LearningPathSummary,
} from "@/lib/api";
import { PathRoadmap } from "@/components/pop/PathRoadmap";
import { Button } from "@/components/pop/Button";
import { Input } from "@/components/pop/Input";

// Learn mode (Friendly Pop). Two variants driven by ?mode=:
//   structured (default) -> /feed?session=...        (path + quiz checkpoints)
//   basic     (?mode=basic) -> /feed?session=...&quiz=off  (just structured videos)
// Both build the same LLM path via createLearningPath + PathRoadmap; only the
// feed destination differs (the feed reads quiz=off to suppress checkpoints).
function LearnContent() {
  const router = useRouter();
  const params = useSearchParams();
  const [basic, setBasic] = useState(params.get("mode") === "basic");
  const { user, session, loading } = useAuth();
  const [query, setQuery] = useState("");
  const [building, setBuilding] = useState(false);
  const [error, setError] = useState("");
  const [path, setPath] = useState<LearningPath | null>(null);
  const [history, setHistory] = useState<LearningPathSummary[]>([]);

  // Append &quiz=off in Basic mode so the feed suppresses quiz checkpoints.
  const feedUrl = (base: string) => `${base}${basic ? "&quiz=off" : ""}`;

  useEffect(() => {
    if (!user || !session) return;
    getUserHistory(user.id, session.access_token).then(setHistory).catch(() => {});
  }, [user?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  async function build(e: React.FormEvent) {
    e.preventDefault();
    const text = query.trim();
    if (!text || building || !user || !session) return;
    setBuilding(true);
    setError("");
    try {
      const p = await createLearningPath(text, user.id, session.access_token);
      setPath(p);
    } catch {
      setError("Couldn't build a path for that. Try rephrasing the topic.");
    } finally {
      setBuilding(false);
    }
  }

  if (loading || !user) return null;

  return (
    <main className="min-h-screen bg-canvas text-on-surface flex flex-col items-center px-6 pt-10 pb-28">
      <div className="w-full max-w-md flex flex-col gap-6">
        {/* Top bar */}
        <div className="flex items-center justify-between">
          <h1 className="font-display text-2xl font-extrabold tracking-tight">
            Curio<span className="text-primary">.</span>
          </h1>
          <Button variant="ghost" size="sm" onClick={() => router.push("/")}>
            Home
          </Button>
        </div>

        {/* Mode toggle — both Structured and Basic reachable from the Learn tab */}
        <div className="flex rounded-pill bg-surface-alt p-1 border border-outline">
          <button
            type="button"
            onClick={() => setBasic(false)}
            className={`flex-1 rounded-pill py-2 text-sm font-semibold transition ${!basic ? "bg-primary text-on-primary shadow-elev-1" : "text-on-surface-muted hover:text-on-surface"}`}
          >
            Structured
          </button>
          <button
            type="button"
            onClick={() => setBasic(true)}
            className={`flex-1 rounded-pill py-2 text-sm font-semibold transition ${basic ? "bg-primary text-on-primary shadow-elev-1" : "text-on-surface-muted hover:text-on-surface"}`}
          >
            Basic
          </button>
        </div>

        {path ? (
          <>
            <PathRoadmap
              path={path}
              onPick={(slug) => router.push(feedUrl(`/feed?topic=${encodeURIComponent(slug)}`))}
              onReset={() => {
                setPath(null);
                setQuery("");
              }}
            />
            <Button size="lg" onClick={() => router.push(feedUrl(`/feed?session=${path.session_id}`))}>
              Start from the beginning →
            </Button>
          </>
        ) : (
          <>
            {/* Hero */}
            <div className="mt-6">
              <span
                className={`inline-block text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-pill mb-2 ${
                  basic
                    ? "bg-surface-alt text-on-surface border border-outline"
                    : "bg-secondary text-on-secondary"
                }`}
              >
                {basic ? "Basic, no quizzes" : "Structured, with quizzes"}
              </span>
              <h2 className="font-display text-3xl font-extrabold leading-tight">
                {basic ? "Basic learn" : "Structured learn"}
              </h2>
              <p className="text-on-surface-muted mt-1.5">
                {basic
                  ? "Tell us what you want to learn. We build an ordered path and serve just the best clips, no questions."
                  : "Tell us what you want to learn. We build an ordered path with quick quiz check-ins along the way."}
              </p>
            </div>

            {/* Topic -> path */}
            <form onSubmit={build} className="flex flex-col gap-3">
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="e.g. hashmaps and dynamic programming"
                disabled={building}
                autoFocus
              />
              <Button type="submit" size="lg" disabled={building || !query.trim()}>
                {building ? "Building your path…" : "Build my path →"}
              </Button>
              {error && <p className="text-danger text-sm font-medium">{error}</p>}
            </form>

            {/* Recent paths — resume a prior curriculum */}
            {history.length > 0 && (
              <div className="flex flex-col gap-2">
                <p className="text-on-surface-muted text-xs font-semibold uppercase tracking-wide">
                  Pick up where you left off
                </p>
                <div className="flex flex-col gap-2">
                  {history.slice(0, 5).map((h) => (
                    <button
                      key={h.session_id}
                      onClick={() => router.push(feedUrl(`/feed?session=${h.session_id}`))}
                      className="text-left bg-surface rounded-card border border-outline shadow-elev-1 px-4 py-3 transition duration-base hover:shadow-elev-2 hover:-translate-y-0.5 motion-reduce:transform-none"
                    >
                      <span className="font-display font-bold text-on-surface block truncate">
                        {h.user_query}
                      </span>
                      <span className="text-on-surface-muted text-xs">
                        {h.topic_count} topic{h.topic_count === 1 ? "" : "s"}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </main>
  );
}

export default function LearnPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-canvas" />}>
      <LearnContent />
    </Suspense>
  );
}
