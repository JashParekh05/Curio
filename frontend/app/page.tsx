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
  const { user, session, loading, isAuthenticated, isGuest, anonFailed, retryGuest, signOut } = useAuth();
  const [seeding, setSeeding] = useState(false);
  const [customTopic, setCustomTopic] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!user || !session) return;
    if (!hasSeenIntro()) {
      router.replace("/welcome");
      return;
    }
    // The Home tab links to /?home=1 to view this launcher; a bare "/" (the app
    // opening) drops a ready user straight into the Discover feed (feed-first).
    const forceLauncher =
      typeof window !== "undefined" &&
      new URLSearchParams(window.location.search).get("home") === "1";
    if (isAuthenticated) {
      getUserProfile(user.id, session.access_token)
        .then((p) => {
          if (!p.onboarding_complete) router.replace("/onboarding");
          else if (!forceLauncher) router.replace("/discover");
        })
        .catch(() => {});
    } else if (!forceLauncher) {
      // guest, intro already seen → open straight into the feed
      router.replace("/discover");
    }
  }, [user, session, isAuthenticated]);

  function toggleTopic(t: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });
  }

  async function startFeed(seeds: string[] = []) {
    if (seeding) return;
    // Optional cold-start seeds: nudge the interest vector before the feed loads.
    if (seeds.length && user && session) {
      setSeeding(true);
      try {
        await setUserInterests(user.id, seeds, session.access_token);
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
        {anonFailed ? (
          <>
            <p className="text-on-surface-muted text-center max-w-xs">
              We couldn&apos;t start a session. Check your connection and try again.
            </p>
            <Button size="lg" onClick={() => retryGuest()}>
              Retry
            </Button>
            <Button variant="soft" size="lg" onClick={() => router.push("/login")}>
              Sign in instead
            </Button>
          </>
        ) : (
          <>
            <p className="text-on-surface-muted text-center max-w-xs">
              A feed that learns what you love, and teaches it to you, one clip at a time.
            </p>
            <Button size="lg" onClick={() => router.push("/login")}>
              Get started
            </Button>
          </>
        )}
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
          <p className="text-on-surface-muted mt-1.5">Three ways to learn. Pick your mode.</p>
        </div>

        {/* Three modes */}
        <div className="flex flex-col gap-3">
          <button
            onClick={() => startFeed(Array.from(selected))}
            disabled={seeding}
            className="group text-left bg-surface rounded-card border border-outline shadow-elev-1 px-5 py-4 transition duration-base hover:shadow-elev-2 hover:-translate-y-0.5 disabled:opacity-50 motion-reduce:transform-none"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="font-display text-lg font-extrabold">Discover</span>
              <span className="text-[10px] font-bold uppercase tracking-wide bg-primary text-on-primary px-2 py-0.5 rounded-pill">For You</span>
            </div>
            <p className="text-on-surface-muted text-sm mt-1">
              {seeding ? "Warming up…" : "Scroll truly random clips. The feed learns what you love."}
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

        {/* Optional cold-start seeds for Discover — pick as many as you like */}
        <div className="flex flex-col gap-2">
          <p className="text-on-surface-muted text-xs font-semibold uppercase tracking-wide">
            Warm your Discover feed (optional)
          </p>
          <div className="flex flex-wrap gap-2">
            {[...INTERESTS, ...Array.from(selected).filter((t) => !INTERESTS.includes(t))].map((i) => {
              const on = selected.has(i);
              return (
                <button
                  key={i}
                  onClick={() => toggleTopic(i)}
                  disabled={seeding}
                  aria-pressed={on}
                  className={`rounded-pill text-sm px-3.5 py-2 border transition duration-base disabled:opacity-50 ${
                    on
                      ? "bg-primary text-on-primary border-primary shadow-elev-1"
                      : "bg-surface-alt text-on-surface border-outline hover:brightness-95"
                  }`}
                >
                  {i}
                </button>
              );
            })}
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const t = customTopic.trim();
              if (!t) return;
              setSelected((prev) => new Set(prev).add(t));
              setCustomTopic("");
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
          {selected.size > 0 && (
            <Button size="lg" onClick={() => startFeed(Array.from(selected))} disabled={seeding} className="mt-1">
              {seeding
                ? "Warming up…"
                : `Start your feed with ${selected.size} topic${selected.size === 1 ? "" : "s"} →`}
            </Button>
          )}
        </div>
      </div>
      <LegalFooter />
    </main>
  );
}
