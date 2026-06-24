"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { getUserProfile, setUserInterests } from "@/lib/api";
import { hasSeenIntro } from "@/lib/intro";
import LegalFooter from "@/components/LegalFooter";
import { Button } from "@/components/pop/Button";

// Passive-first home (Friendly Pop). The product is the interest-learning feed:
// you scroll, it picks up what you love from signals (watch time, 🔥/✓, skips)
// and serves the best auto-curated clips. The active path/quiz flow is parked
// (its files remain, just unlinked). Optional interest seeds warm the cold start.
const INTERESTS = [
  "Coding",
  "Biology",
  "History",
  "Space",
  "Finance",
  "Psychology",
  "Design",
  "Math",
];

export default function Home() {
  const router = useRouter();
  const { user, session, loading, isAuthenticated, isGuest, signOut } = useAuth();
  const [seeding, setSeeding] = useState(false);

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
    <main className="min-h-screen bg-canvas text-on-surface flex flex-col items-center px-6 py-10">
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
          <h2 className="font-display text-3xl font-extrabold leading-tight">
            Your learning feed
          </h2>
          <p className="text-on-surface-muted mt-1.5">
            Scroll short clips on what you&apos;re into — Curio learns what you love as you
            watch and keeps serving the best.
          </p>
        </div>

        {/* Primary passive action */}
        <Button size="lg" onClick={() => startFeed()} disabled={seeding}>
          {seeding ? "Warming up…" : "Start your feed →"}
        </Button>

        {/* Optional cold-start seeds */}
        <div className="flex flex-col gap-2">
          <p className="text-on-surface-muted text-xs font-semibold uppercase tracking-wide">
            Into anything specific? (optional)
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
        </div>
      </div>
      <LegalFooter />
    </main>
  );
}
