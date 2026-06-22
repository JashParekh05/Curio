"use client";

import { useMemo } from "react";
import type { FeedLevel } from "@/lib/api";

// The four Canonical_Arc beats. Used only to render the per-topic beat dots for
// the currently active topic so the learner can see how far into the lesson
// (hook -> what -> how -> outcomes) they are.
const BEAT_LABELS = ["Hook", "What", "How", "Outcomes"];

function titleCase(slug: string): string {
  return slug
    .split("-")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export interface LevelStepperProps {
  // The serialized LeveledPath in ordinal order. When empty the stepper renders
  // nothing (caller falls back to the legacy flat list).
  levels: FeedLevel[];
  // Optional human-readable names per topic slug; falls back to title-casing.
  topicNames?: Record<string, string>;
  // The topic the learner is currently watching.
  activeSlug: string;
  // The active beat (section_index 0..3) within the active topic, or null.
  activeSection: number | null;
  // Topics the learner has reached/seen — drives the lightweight feed-position
  // progress used ONLY as a fallback when real mastery is unavailable.
  coveredSlugs: Set<string>;
  // Real per-topic mastery from GET /api/progress, keyed by topic slug. When
  // present it drives the topic dots + MASTERED/RECOMMENDED badges; when absent
  // the stepper degrades to the feed-position `coveredSlugs` signal (no
  // regression). `unlock` is advisory only and NEVER disables navigation.
  topicMastery?: Record<string, { mastered: boolean; unlock: string }>;
  // Real per-level percent_complete (0..100) keyed by level ordinal, from
  // GET /api/progress. Overrides the feed-position bar when present.
  levelPercent?: Record<number, number>;
  // Jump to a topic (sectionIndex null = topic start). ALWAYS enabled for every
  // level — later levels are navigable (soft, never locked).
  onJump: (slug: string, sectionIndex: number | null) => void;
}

/**
 * Level -> Topic -> Beat stepper (Phase 1, Req 1.1, 4.2; Phase 3, Req 3.3, 4.2).
 *
 * Renders the LeveledPath as ordered bands (Foundations -> Core -> Advanced),
 * each listing its topics with a per-level progress indicator. When real mastery
 * from `GET /api/progress` is supplied (`topicMastery`/`levelPercent`, task
 * 19.1) the bar shows mastered/total and the topics carry MASTERED/RECOMMENDED
 * badges; otherwise it falls back to the lightweight feed-position progress
 * (topics covered / total) so there is no regression when progress is
 * unavailable. The active topic is highlighted and its beat position is shown as
 * dots. Every level and topic is navigable — the stepper never locks or disables
 * a later level, and `unlock` is advisory only (soft progression).
 */
export default function LevelStepper({
  levels,
  topicNames,
  activeSlug,
  activeSection,
  coveredSlugs,
  topicMastery,
  levelPercent,
  onJump,
}: LevelStepperProps) {
  // Which level contains the active topic, so we can highlight the band header.
  const activeOrdinal = useMemo(() => {
    const lvl = levels.find((l) => l.topic_slugs.includes(activeSlug));
    return lvl?.ordinal ?? null;
  }, [levels, activeSlug]);

  // True once real mastery is available — switches the bar + badges from the
  // feed-position fallback to mastery-driven progress.
  const hasMastery = !!topicMastery && Object.keys(topicMastery).length > 0;

  if (!levels || levels.length === 0) return null;

  return (
    <div className="space-y-4">
      {levels.map((level) => {
        const total = level.topic_slugs.length;
        const covered = level.topic_slugs.filter((s) => coveredSlugs.has(s)).length;
        const mastered = level.topic_slugs.filter((s) => topicMastery?.[s]?.mastered).length;
        const isActiveLevel = level.ordinal === activeOrdinal;
        // Mastery-driven percent when available (prefer the server's
        // percent_complete, else mastered/total); feed-position percent otherwise.
        const pct = hasMastery
          ? levelPercent?.[level.ordinal] ??
            (total > 0 ? Math.round((mastered / total) * 100) : 0)
          : total > 0
            ? Math.round((covered / total) * 100)
            : 0;
        const countLabel = hasMastery ? `${mastered}/${total}` : `${covered}/${total}`;

        return (
          <div
            key={level.ordinal}
            className={`brutal bg-white ${isActiveLevel ? "shadow-brutal" : ""}`}
          >
            {/* Level header: ordinal, name, and progress (mastery or position). */}
            <div
              className={`px-3 py-2.5 border-b-[3px] border-ink ${
                isActiveLevel ? "bg-accent-yellow" : "bg-paper"
              }`}
            >
              <div className="flex items-center gap-2">
                <span className="bg-ink text-paper w-5 h-5 flex items-center justify-center text-xs font-black shrink-0">
                  {level.ordinal}
                </span>
                <p className="flex-1 min-w-0 text-sm font-black truncate text-ink">{level.name}</p>
                <span className="text-ink/60 text-xs font-bold tabular-nums shrink-0">
                  {countLabel}
                </span>
              </div>
              {/* Per-level progress bar (mastery-driven, else feed-position). */}
              <div className="mt-2 h-1.5 bg-ink/15">
                <div
                  className="h-full bg-accent-lime transition-all duration-300"
                  style={{ width: `${pct}%` }}
                />
              </div>
            </div>

            {/* Topics in this level, in path order. Always navigable. */}
            <div className="divide-y-[3px] divide-ink">
              {level.topic_slugs.map((slug) => {
                const isActiveTopic = slug === activeSlug;
                const tm = topicMastery?.[slug];
                const isMastered = hasMastery ? !!tm?.mastered : false;
                const isRecommended = hasMastery && tm?.unlock === "recommended" && !isMastered;
                // Topic dot fill: mastery-driven when available, else covered.
                const filled = hasMastery ? isMastered : coveredSlugs.has(slug);
                const name = topicNames?.[slug] ?? titleCase(slug);
                return (
                  <div key={slug} className={isActiveTopic ? "bg-accent-cyan" : "bg-white"}>
                    <button
                      onClick={() => onJump(slug, null)}
                      className="w-full flex items-center gap-2 text-left px-3 py-2 hover:bg-accent-yellow transition-colors"
                    >
                      <span
                        className={`border-2 border-ink w-3.5 h-3.5 shrink-0 ${
                          filled ? "bg-accent-lime" : "bg-white"
                        }`}
                        aria-hidden
                      />
                      <p className="flex-1 min-w-0 text-xs font-bold truncate text-ink">{name}</p>
                      {isMastered && (
                        <span className="brutal bg-accent-lime text-ink text-[9px] font-black px-1.5 py-0.5 shrink-0">
                          MASTERED
                        </span>
                      )}
                      {isRecommended && (
                        <span className="brutal bg-accent-orange text-ink text-[9px] font-black px-1.5 py-0.5 shrink-0">
                          NEXT UP
                        </span>
                      )}
                      {isActiveTopic && (
                        <span className="brutal bg-ink text-paper text-[9px] font-black px-1.5 py-0.5 shrink-0">
                          NOW
                        </span>
                      )}
                    </button>

                    {/* Beat dots for the active topic — the "Beat" tier of the
                        stepper. Shows hook/what/how/outcomes and the current
                        beat; each is navigable. */}
                    {isActiveTopic && (
                      <div className="flex items-center gap-1.5 px-3 pb-2">
                        {BEAT_LABELS.map((label, beat) => {
                          const onBeat = activeSection === beat;
                          return (
                            <button
                              key={beat}
                              onClick={() => onJump(slug, beat)}
                              title={label}
                              aria-label={`${label} beat`}
                              className={`flex-1 h-1.5 transition-colors ${
                                onBeat ? "bg-accent-pink" : "bg-ink/20 hover:bg-ink/40"
                              }`}
                            />
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
