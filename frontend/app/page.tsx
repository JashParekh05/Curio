"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createLearningPath, type LearningPath } from "@/lib/api";

const SUGGESTIONS = [
  "I want to learn hashmaps and binary trees",
  "Teach me cell biology from scratch",
  "Explain machine learning basics",
  "I need to understand calculus derivatives",
];

export default function Home() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [path, setPath] = useState<LearningPath | null>(null);
  const [error, setError] = useState("");

  async function handleSubmit(q: string) {
    const trimmed = q.trim();
    if (!trimmed) return;
    setLoading(true);
    setError("");
    try {
      const result = await createLearningPath(trimmed);
      setPath(result);
    } catch {
      setError("Something went wrong. Is the backend running?");
    } finally {
      setLoading(false);
    }
  }

  function startWatching() {
    if (path) router.push(`/feed?session=${path.session_id}`);
  }

  return (
    <main className="min-h-screen bg-black text-white flex flex-col items-center justify-center px-4">
      <div className="w-full max-w-xl space-y-8">
        <div className="text-center space-y-2">
          <h1 className="text-4xl font-bold tracking-tight">LearnReel</h1>
          <p className="text-zinc-400 text-lg">What do you want to learn today?</p>
        </div>

        {!path ? (
          <>
            <div className="flex gap-2">
              <input
                className="flex-1 bg-zinc-900 border border-zinc-700 rounded-xl px-4 py-3 text-white placeholder-zinc-500 focus:outline-none focus:border-zinc-400"
                placeholder="e.g. I want to learn hashmaps and dynamic programming"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleSubmit(query)}
                disabled={loading}
              />
              <button
                onClick={() => handleSubmit(query)}
                disabled={loading || !query.trim()}
                className="bg-white text-black font-semibold px-5 py-3 rounded-xl disabled:opacity-40 hover:bg-zinc-100 transition"
              >
                {loading ? "..." : "Go"}
              </button>
            </div>

            <div className="space-y-2">
              <p className="text-zinc-500 text-sm">Try:</p>
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => { setQuery(s); handleSubmit(s); }}
                  className="block w-full text-left text-zinc-400 hover:text-white text-sm px-3 py-2 rounded-lg hover:bg-zinc-900 transition"
                >
                  {s}
                </button>
              ))}
            </div>

            {error && <p className="text-red-400 text-sm text-center">{error}</p>}
          </>
        ) : (
          <div className="space-y-6">
            <div className="bg-zinc-900 rounded-2xl p-5 space-y-4">
              <p className="text-zinc-300 text-sm">{path.summary}</p>
              <div className="space-y-3">
                {path.topics.map((topic, i) => (
                  <div key={topic.slug} className="flex items-start gap-3">
                    <span className="text-zinc-600 text-sm mt-0.5 w-5">{i + 1}.</span>
                    <div>
                      <p className="font-medium">{topic.name}</p>
                      <p className="text-zinc-500 text-xs capitalize">{topic.difficulty}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <button
              onClick={startWatching}
              className="w-full bg-white text-black font-semibold py-4 rounded-xl text-lg hover:bg-zinc-100 transition"
            >
              Start Watching →
            </button>
            <button
              onClick={() => { setPath(null); setQuery(""); }}
              className="w-full text-zinc-500 hover:text-white text-sm py-2 transition"
            >
              Start over
            </button>
          </div>
        )}
      </div>
    </main>
  );
}
