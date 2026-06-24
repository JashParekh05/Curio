"use client";

// GameHomeHub — the redesigned front door. Instead of a bare search box, the home
// page is a Pokémon-GO / Duolingo-style "lobby": the learner sees their identity
// (level ring + streak flame), a living overworld trail, a daily quest that
// protects the streak, and one obvious primary action — name a quest and drop
// straight into /play. Built on the app's existing brutalist tokens (so it stays
// broadly marketable as general education) with Framer Motion juice, and fully
// reduced-motion safe.
//
// Retention principles wired in (research-backed):
//  - Streaks (Duolingo: 7-day streak -> ~3.6x more likely to continue): a flame
//    + day count, with a daily-quest card that frames "keep your streak".
//  - Value on the first screen: status + a single obvious next step, no walls of text.
//  - Visible progress: XP/level ring from the existing xpLevelStats curve.
//  - Map-as-hub (Pokémon GO): an ambient world trail you tap to enter.

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { motion, useReducedMotion, type Variants } from "motion/react";
import { useAuth } from "@/lib/auth-context";
import { xpLevelStats } from "@/components/XpHud";
import {
  getPlayerStats,
  recordQuestStart,
  isActiveToday,
  defaultPlayerStats,
  type PlayerStats,
} from "@/lib/player-stats";
import {
  getActiveGameSessionId,
  restoreGameSession,
  type GameSessionState,
} from "@/lib/game-progress";
import LegalFooter from "@/components/LegalFooter";

const SUGGESTIONS = [
  "I want to learn about binary trees",
  "Teach me cell biology from scratch",
  "Teach me about World War 2",
  "I need to understand calculus derivatives",
];

// Decorative overworld biomes (echo the Pixel Quest spec: meadow -> forest ->
// caverns -> peaks -> the keep). Purely cosmetic on the hub trail.
const BIOMES = [
  { label: "Meadow", color: "bg-accent-lime", emoji: "🌱" },
  { label: "Forest", color: "bg-accent-cyan", emoji: "🌲" },
  { label: "Caverns", color: "bg-accent-purple", emoji: "💎" },
  { label: "Sky Peaks", color: "bg-accent-orange", emoji: "⛰️" },
  { label: "The Keep", color: "bg-accent-pink", emoji: "🏰" },
] as const;

interface ActiveQuest {
  goal: string;
  cleared: number;
  total: number;
}

function summarizeSession(s: GameSessionState | null): ActiveQuest | null {
  if (!s || !s.goal) return null;
  const nodes = s.nodes ? Object.values(s.nodes) : [];
  const cleared = nodes.filter(
    (n) => n.state === "CLEARED" || n.state === "MASTERED",
  ).length;
  const total = Math.max(nodes.length, s.path?.length ?? 0, 1);
  return { goal: s.goal, cleared, total };
}

export default function GameHomeHub() {
  const router = useRouter();
  const { user, isGuest, signOut } = useAuth();
  const reduce = useReducedMotion();

  const [stats, setStats] = useState<PlayerStats>(defaultPlayerStats());
  const [active, setActive] = useState<ActiveQuest | null>(null);
  const [sessionXp, setSessionXp] = useState(0);
  const [query, setQuery] = useState("");
  const [mounted, setMounted] = useState(false);

  // Read client-only stores after mount to avoid SSR hydration mismatch.
  useEffect(() => {
    setMounted(true);
    setStats(getPlayerStats());
    const id = getActiveGameSessionId();
    if (id) {
      const s = restoreGameSession(id);
      setActive(summarizeSession(s));
      setSessionXp(s?.xp ?? 0);
    }
  }, []);

  // The level ring reflects real earned XP: lifetime aggregate, or the in-progress
  // quest's XP if that's higher (so it's honest without touching the play loop).
  const displayXp = Math.max(stats.lifetimeXp, sessionXp);
  const lvl = useMemo(() => xpLevelStats(displayXp), [displayXp]);
  const streakSecured = mounted && isActiveToday(stats);

  function startQuest(q: string) {
    const trimmed = q.trim();
    if (!trimmed) return;
    recordQuestStart(trimmed);
    router.push(`/play?topic=${encodeURIComponent(trimmed)}`);
  }

  // Stagger children in on load (skipped under reduced motion).
  const container: Variants = reduce
    ? {}
    : {
        hidden: { opacity: 0 },
        show: { opacity: 1, transition: { staggerChildren: 0.07, delayChildren: 0.05 } },
      };
  const item: Variants = reduce
    ? {}
    : {
        hidden: { opacity: 0, y: 14 },
        show: { opacity: 1, y: 0, transition: { type: "spring", stiffness: 260, damping: 22 } },
      };

  return (
    <main className="min-h-screen bg-paper text-ink flex flex-col items-center px-4 py-8">
      <motion.div
        className="w-full max-w-xl space-y-5"
        variants={container}
        initial={reduce ? undefined : "hidden"}
        animate={reduce ? undefined : "show"}
      >
        {/* Top bar: wordmark + identity + account actions */}
        <motion.div variants={item} className="flex items-end justify-between">
          <div>
            <h1 className="text-4xl font-black tracking-tight leading-none">
              Curio<span className="text-accent-pink">.</span>
            </h1>
            <div className="mt-2 inline-block bg-accent-lime brutal px-2 py-0.5 text-xs font-bold">
              {isGuest ? "GUEST EXPLORER" : user?.email}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => router.push("/welcome")}
              className="text-ink/50 hover:text-ink text-xs font-bold transition"
            >
              How it works
            </button>
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
        </motion.div>

        {/* Player status card: level ring + streak flame */}
        <motion.section
          variants={item}
          className="brutal-card bg-white px-4 py-4 flex items-center gap-4"
          aria-label="Your progress"
        >
          <XpRing level={lvl.level} progress={lvl.progress} reduce={!!reduce} />
          <div className="flex-1 min-w-0">
            <p className="text-[10px] font-black uppercase tracking-widest text-ink/50">
              Adventurer
            </p>
            <p className="text-lg font-black leading-tight truncate">
              {isGuest ? "Guest Explorer" : (user?.email?.split("@")[0] ?? "Explorer")}
            </p>
            <p className="text-xs font-bold text-ink/60 tabular-nums">
              {lvl.xpIntoLevel} / {lvl.xpForLevel} XP · {displayXp} total
            </p>
          </div>
          <StreakBadge streak={mounted ? stats.streak : 0} secured={streakSecured} reduce={!!reduce} />
        </motion.section>

        {/* Hero: the overworld + the one primary action */}
        <motion.section variants={item} className="brutal-card bg-paper overflow-hidden">
          <div className="px-4 pt-4">
            <p className="text-2xl font-black leading-tight">
              {active ? "Your world awaits" : "Begin your adventure"}
            </p>
            <p className="text-sm font-medium text-ink/60 mt-0.5">
              {active
                ? "Pick up where you left off, or chart a new quest."
                : "Name anything you want to learn — we build the world around it."}
            </p>
          </div>

          <WorldTrail position={active ? active.cleared : 0} reduce={!!reduce} />

          {active && (
            <div className="px-4 -mt-1">
              <button
                onClick={() => router.push("/play")}
                className="brutal-btn w-full bg-accent-lime text-ink py-3 text-base flex items-center justify-between"
              >
                <span className="truncate">
                  ▶ Continue: <span className="font-black">{active.goal}</span>
                </span>
                <span className="text-xs font-black tabular-nums shrink-0 ml-2">
                  {active.cleared}/{active.total}
                </span>
              </button>
            </div>
          )}

          <div className="p-4 space-y-2">
            <p className="text-ink/60 text-xs font-black uppercase tracking-wide">
              {active ? "Or start a new quest" : "Name your quest"}
            </p>
            <div className="flex gap-2">
              <input
                className="brutal flex-1 bg-white px-4 py-3 text-ink placeholder-ink/40 focus:outline-none focus:shadow-brutal font-medium"
                placeholder="e.g. hashmaps and dynamic programming"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && startQuest(query)}
                aria-label="What do you want to learn?"
              />
              <motion.button
                onClick={() => startQuest(query)}
                disabled={!query.trim()}
                className="brutal-btn bg-accent-yellow text-ink px-6 py-3 disabled:opacity-40"
                animate={reduce || !query.trim() ? undefined : { scale: [1, 1.05, 1] }}
                transition={reduce ? undefined : { duration: 1.4, repeat: Infinity }}
              >
                Go
              </motion.button>
            </div>
          </div>
        </motion.section>

        {/* Daily quest — the streak protector (return-rhythm trigger) */}
        <motion.div variants={item}>
          <DailyQuestCard
            streak={mounted ? stats.streak : 0}
            secured={streakSecured}
            onGo={() => startQuest(stats.lastTopic || SUGGESTIONS[0])}
          />
        </motion.div>

        {/* Quest suggestions */}
        <motion.div variants={item} className="space-y-2">
          <p className="text-ink/60 text-xs font-black uppercase tracking-wide">Quick quests</p>
          <div className="grid grid-cols-1 gap-2">
            {SUGGESTIONS.map((s, i) => (
              <button
                key={s}
                onClick={() => startQuest(s)}
                className={`brutal-btn ${["bg-accent-yellow", "bg-accent-cyan", "bg-accent-lime", "bg-accent-pink"][i % 4]} text-ink text-left text-sm px-3 py-2.5`}
              >
                {s}
              </button>
            ))}
          </div>
        </motion.div>

        {/* Quiet secondary entry: the Discover feed, framed as exploration */}
        <motion.button
          variants={item}
          onClick={() => router.push("/discover")}
          className="brutal-btn w-full bg-white text-ink py-3 text-sm flex items-center justify-center gap-2"
        >
          🧭 Explore the wild <span className="font-black">{">"}</span>
        </motion.button>
      </motion.div>
      <LegalFooter />
    </main>
  );
}

// ---------------------------------------------------------------------------
// Sub-components (in-file: cohesive, not reused elsewhere)
// ---------------------------------------------------------------------------

// Circular XP/level ring. SVG arc whose fill tracks intra-level progress, with
// the current level in the center. Pure CSS transition for the fill; the parent
// handles entrance motion.
function XpRing({
  level,
  progress,
  reduce,
}: {
  level: number;
  progress: number;
  reduce: boolean;
}) {
  const r = 30;
  const c = 2 * Math.PI * r;
  const pct = Math.min(1, Math.max(0, progress));
  const offset = c * (1 - pct);
  return (
    <div className="relative shrink-0" style={{ width: 76, height: 76 }} aria-label={`Level ${level}`}>
      <svg width={76} height={76} viewBox="0 0 76 76">
        <circle cx={38} cy={38} r={r} fill="none" stroke="#11111122" strokeWidth={7} />
        <circle
          cx={38}
          cy={38}
          r={r}
          fill="none"
          stroke="#A8E80C"
          strokeWidth={7}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={offset}
          transform="rotate(-90 38 38)"
          style={{ transition: reduce ? undefined : "stroke-dashoffset 700ms ease-out" }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center leading-none">
        <span className="text-[8px] font-black uppercase tracking-widest text-ink/50">Lvl</span>
        <span className="text-2xl font-black">{level}</span>
      </div>
    </div>
  );
}

// Streak flame + day count. The flame flickers continuously (disabled under
// reduced motion) and dims when today's streak is not yet secured.
function StreakBadge({
  streak,
  secured,
  reduce,
}: {
  streak: number;
  secured: boolean;
  reduce: boolean;
}) {
  return (
    <div
      className={`brutal flex flex-col items-center justify-center px-3 py-2 shrink-0 ${
        secured ? "bg-accent-orange" : "bg-white"
      }`}
      aria-label={`${streak} day streak`}
      title={secured ? "Streak secured today" : "Learn today to keep your streak"}
    >
      <motion.span
        className={`text-2xl leading-none ${secured ? "" : "grayscale opacity-50"}`}
        animate={reduce || !secured ? undefined : { scale: [1, 1.15, 0.98, 1.08, 1] }}
        transition={reduce ? undefined : { duration: 1.6, repeat: Infinity, ease: "easeInOut" }}
      >
        🔥
      </motion.span>
      <span className="text-base font-black leading-none mt-0.5 tabular-nums">{streak}</span>
      <span className="text-[8px] font-black uppercase tracking-widest text-ink/60">
        {streak === 1 ? "day" : "days"}
      </span>
    </div>
  );
}

// Ambient overworld trail: biome stops along a path with a bobbing position
// marker. Decorative — the "map-as-hub" cue that this is a game.
function WorldTrail({ position, reduce }: { position: number; reduce: boolean }) {
  const here = Math.min(BIOMES.length - 1, Math.max(0, position));
  return (
    <div className="relative px-4 py-5">
      {/* connecting path */}
      <div className="absolute left-7 right-7 top-1/2 h-[3px] bg-ink/20" aria-hidden />
      <div className="relative flex items-center justify-between">
        {BIOMES.map((b, i) => {
          const reached = i <= here;
          const isHere = i === here;
          return (
            <div key={b.label} className="flex flex-col items-center gap-1 relative">
              <motion.div
                className={`brutal ${reached ? b.color : "bg-white"} w-10 h-10 flex items-center justify-center text-lg ${
                  reached ? "" : "opacity-40 grayscale"
                }`}
                animate={reduce || !isHere ? undefined : { y: [0, -5, 0] }}
                transition={reduce ? undefined : { duration: 1.4, repeat: Infinity, ease: "easeInOut" }}
              >
                {b.emoji}
              </motion.div>
              <span
                className={`text-[8px] font-black uppercase tracking-wide ${
                  reached ? "text-ink" : "text-ink/40"
                }`}
              >
                {b.label}
              </span>
              {isHere && (
                <span className="absolute -top-3 text-sm" aria-label="You are here">
                  🧭
                </span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// Daily quest card: the streak protector. Frames today's single action as
// "keep your streak", the highest-ROI return-rhythm trigger from the research.
function DailyQuestCard({
  streak,
  secured,
  onGo,
}: {
  streak: number;
  secured: boolean;
  onGo: () => void;
}) {
  return (
    <section
      className={`brutal-card px-4 py-3 flex items-center gap-3 ${
        secured ? "bg-accent-lime" : "bg-accent-yellow"
      }`}
      aria-label="Daily quest"
    >
      <span className="text-2xl shrink-0">{secured ? "✅" : "🗡️"}</span>
      <div className="flex-1 min-w-0">
        <p className="text-[10px] font-black uppercase tracking-widest text-ink/60">Daily quest</p>
        <p className="text-sm font-black leading-tight">
          {secured
            ? `Streak secured! Come back tomorrow for day ${streak + 1}.`
            : streak > 0
              ? `One lesson keeps your ${streak}-day streak alive.`
              : "Finish one lesson to light your first streak."}
        </p>
      </div>
      {!secured && (
        <button onClick={onGo} className="brutal-btn bg-white text-ink text-sm px-3 py-2 shrink-0">
          Go
        </button>
      )}
    </section>
  );
}
