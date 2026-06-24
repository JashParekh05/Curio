"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
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

// Learn mode (Friendly Pop) — the deliberate counterpart to the passive Discover
// feed. Type a topic -> the LLM builds an ordered path -> tap any step to watch
// that topic's clips, or start the full guided path (multi-topic + quiz beats).
// Reuses the existing createLearningPath + PathRoadmap; nothing new on the
// backend. This is the front door that the passive-first pivot had unlinked.
export default function LearnPage() {
  const router = useRouter();
  const { user, session, loading } = useAuth();
  const [query, setQuery] = useState("");
  const [building, setBuilding] = useState(false);
  const [error, setError] = useState("");
  const [path, setPath] = useState<LearningPath | null>(null);
  const [history, setHistory] = useState<LearningPathSummary[]>([]);

  // Recent paths (best-effort; empty for fresh guests). Keyed on the primitive
  // user id so token refreshes don't refetch — same discipline as the feed.
  useEffect(() => {
    if (!user || !session) return;
    getUserHistory(user.id, session.access_token).then(setHistory).catch(() => {});
  }, [user?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  async function build(e: React.FormEvent) {
    e.preventDefault();
    const q = query.trim();
    if (!q || building || !user || !session) return;
    setBuilding(true);
    setError("");
    try {
      const p = await createLearningPath(q, user.id, session.access_token);
      setPath(p);
    } catch {
      setError("Couldn't build a path for that. Try rephrasing the topic.");
    } finally {
      setBuilding(false);
    }
  }

  if (loading || !user) return null;

  return (
    <main className="min-h-screen bg-canvas text-on-surface flex flex-col items-center px-6 py-10">
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

        {path ? (
          <>
            <PathRoadmap
              path={path}
              onPick={(slug) => router.push(`/feed?topic=${encodeURIComponent(slug)}`)}
              onReset={() => {
                setPath(null);
                setQuery("");
              }}
            />
            <Button size="lg" onClick={() => router.push(`/feed?session=${path.session_id}`)}>
              Start from the beginning →
            </Button>
          </>
        ) : (
          <>
            {/* Hero */}
            <div className="mt-6">
              <h2 className="font-display text-3xl font-extrabold leading-tight">
                Learn something
              </h2>
              <p className="text-on-surface-muted mt-1.5">
                Tell us what you want to learn. Curio builds an ordered path and cuts the
                best clips for each step.
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
                      onClick={() => router.push(`/feed?session=${h.session_id}`)}
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
