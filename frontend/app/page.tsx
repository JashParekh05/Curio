"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { getUserProfile, setUserInterests } from "@/lib/api";
import { hasSeenIntro } from "@/lib/intro";
import LegalFooter from "@/components/LegalFooter";
import { Button } from "@/components/pop/Button";
import { Input } from "@/components/pop/Input";

// Passive-first home (Friendly Pop). The product is the interest-learning feed:
// you scroll, it picks up what you love from signals (watch time, 🔥/✓, skips)
// and serves the best auto-curated clips. The active path/quiz flow is parked
// (its files remain, just unlinked). Optional interest seeds warm the cold start.
const INTERESTS = [
  "Coding",
  "AI",
  "Math",
  "Physics",
  "Biology",
  "Chemistry",
  "Space",
  "History",
  "Geography",
  "Economics",
  "Finance",
  "Psychology",
  "Philosophy",
  "Art",
  "Music",
  "Writing",
  "Design",
  "Health",
];

export default function Home() {
  const router = useRouter();
  const { user, session, loading, isAuthenticated, isGuest, signOut } = useAuth();
  const [seeding, setSeeding] = useState(false);
  const [customTopic, setCustomTopic] = useState("");

  useEffect(() => {
    if (!user || !session) return;
    if (!hasSeenIntro()) {
      router.replace("/welcome");
      return;
    }
    if (isAuthenticated) {
      getUserProfile(user.id, session.access_token)
        .then((p) => {
          if (!p.onboarding_complete) router.replace("/onboarding");
        })
        .catch(() => {});
    }
  }, [user, session, isAuthenticated]);

  async function startFeed(seed?: string) {
    if (seeding) return;
    // Optional cold-start seed: nudge the interest vector before the feed loads.
    if (seed && user && session) {
      setSeeding(true);
      try {
        await setUserInterests(user.id, [seed], session.access_token);
      } catch {
        /* non-blocking — the feed still learns from scroll signals */
      }
    }
    router.push("/discover");
  }

  if (loading) return null;

  if (!user) {
    return (
      <main className="min-h-screen bg-canvas text-on-surface flex flex-col items-center justify-center px-6 gap-6">
        <h1 className="font-display text-5xl font-extrabold tracking-tight">
          Curio<span className="text-primary">.</span>
        </h1>
        <p className="text-on-surface-muted text-center max-w-xs">
          A feed that learns what you love — and teaches it to you, one clip at a time.
        </p>
        <Button size="lg" onClick={() => router.push("/login")}>
          Get started
        </Button>
        <LegalFooter />
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-canvas text-on-surface flex flex-col items-center px-6 pt-10 pb-28">
      <div className="w-full max-w-md flex flex-col gap-6">
        {/* Top bar */}
        <div className="flex items-center justify-between">
          <h1 className="font-display text-2xl font-extrabold tracking-tight">
            Curio<span className="text-primary">.</span>
          </h1>
          {isGuest ? (
            <Button variant="ghost" size="sm" onClick={() => router.push("/login")}>
              Save progress
            </Button>
          ) : (
            <Button variant="ghost" size="sm" onClick={signOut}>
              Sign out
            </Button>
          )}
        </div>

        {/* Hero */}
        <div className="mt-6">
          <h2 className="font-display text-3xl font-extrabold leading-tight">What do you want to do?</h2>
          <p className="text-on-surface-muted mt-1.5">Three ways to learn — pick your mode.</p>
        </div>

        {/* Three modes */}
        <div className="flex flex-col gap-3">
          <button
            onClick={() => startFeed()}
            disabled={seeding}
            className="group text-left bg-surface rounded-card border border-outline shadow-elev-1 px-5 py-4 transition duration-base hover:shadow-elev-2 hover:-translate-y-0.5 disabled:opacity-50 motion-reduce:transform-none"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="font-display text-lg font-extrabold">Discover</span>
              <span className="text-[10px] font-bold uppercase tracking-wide bg-primary text-on-primary px-2 py-0.5 rounded-pill">For You</span>
            </div>
            <p className="text-on-surface-muted text-sm mt-1">
              {seeding ? "Warming up…" : "Scroll truly random clips — the feed learns what you love."}
            </p>
          </button>

          <button
            onClick={() => router.push("/learn")}
            className="group text-left bg-surface rounded-card border border-outline shadow-elev-1 px-5 py-4 transition duration-base hover:shadow-elev-2 hover:-translate-y-0.5 motion-reduce:transform-none"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="font-display text-lg font-extrabold">Structured Learn</span>
              <span className="text-[10px] font-bold uppercase tracking-wide bg-secondary text-on-secondary px-2 py-0.5 rounded-pill">Quizzes</span>
            </div>
            <p className="text-on-surface-muted text-sm mt-1">Pick a topic → a guided path with quick quiz check-ins.</p>
          </button>

          <button
            onClick={() => router.push("/learn?mode=basic")}
            className="group text-left bg-surface rounded-card border border-outline shadow-elev-1 px-5 py-4 transition duration-base hover:shadow-elev-2 hover:-translate-y-0.5 motion-reduce:transform-none"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="font-display text-lg font-extrabold">Basic Learn</span>
              <span className="text-[10px] font-bold uppercase tracking-wide bg-surface-alt text-on-surface border border-outline px-2 py-0.5 rounded-pill">Videos</span>
            </div>
            <p className="text-on-surface-muted text-sm mt-1">Pick a topic → just the best clips, structured, no questions.</p>
          </button>
        </div>

        {/* Optional cold-start seeds for Discover */}
        <div className="flex flex-col gap-2">
          <p className="text-on-surface-muted text-xs font-semibold uppercase tracking-wide">
            Warm your Discover feed (optional)
          </p>
          <div className="flex flex-wrap gap-2">
            {INTERESTS.map((i) => (
              <button
                key={i}
                onClick={() => startFeed(i)}
                disabled={seeding}
                className="rounded-pill bg-surface-alt text-on-surface text-sm px-3.5 py-2 border border-outline transition duration-base hover:brightness-95 disabled:opacity-50"
              >
                {i}
              </button>
            ))}
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const t = customTopic.trim();
              if (t) startFeed(t);
            }}
            className="flex gap-2 pt-1"
          >
            <Input
              value={customTopic}
              onChange={(e) => setCustomTopic(e.target.value)}
              placeholder="Add your own topic…"
              disabled={seeding}
              className="flex-1"
            />
            <Button type="submit" variant="soft" disabled={seeding || !customTopic.trim()}>
              Add
            </Button>
          </form>
        </div>
      </div>
      <LegalFooter />
    </main>
  );
}
