"use client";

import { type LearningPath } from "@/lib/api";
import { Button } from "./Button";

// PathRoadmap — a clean, modern visualization of a learning path: an ordered
// vertical stepper of topics (NOT a game world-map). Each step is a tappable
// card that opens that topic's clips + quiz in the feed. No knight/dragon/XP.

const DIFFICULTY: Record<string, { label: string; cls: string }> = {
  beginner: { label: "Beginner", cls: "bg-success text-white" },
  intermediate: { label: "Intermediate", cls: "bg-warning text-on-accent" },
  advanced: { label: "Advanced", cls: "bg-secondary text-on-secondary" },
};

export function PathRoadmap({
  path,
  onPick,
  onReset,
}: {
  path: LearningPath;
  onPick: (slug: string) => void;
  onReset: () => void;
}) {
  const startIndex = Math.max(
    0,
    Math.min(path.topics.length - 1, path.suggested_start_index ?? 0),
  );

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-on-surface-muted text-xs font-semibold uppercase tracking-wide">
            Your learning path
          </p>
          <h2 className="font-display text-2xl font-extrabold leading-tight">
            {path.user_query}
          </h2>
          {path.summary && (
            <p className="text-on-surface-muted text-sm mt-1">{path.summary}</p>
          )}
        </div>
        <Button variant="ghost" size="sm" onClick={onReset}>
          New
        </Button>
      </div>

      <ol className="relative flex flex-col gap-3">
        {/* Clean vertical connector behind the step nodes. */}
        <div className="absolute left-[18px] top-4 bottom-4 w-0.5 bg-outline" aria-hidden />
        {path.topics.map((t, i) => {
          const d = DIFFICULTY[t.difficulty] ?? DIFFICULTY.beginner;
          const isStart = i === startIndex;
          return (
            <li key={t.slug} className="relative">
              <button
                onClick={() => onPick(t.slug)}
                className="group w-full text-left flex gap-3 items-stretch focus-visible:outline-none"
                aria-label={`Start: ${t.name}`}
              >
                <span className="relative z-10 shrink-0 grid place-items-center w-9 h-9 rounded-pill bg-primary text-on-primary font-bold text-sm shadow-elev-1">
                  {i + 1}
                </span>
                <span className="flex-1 min-w-0 bg-surface rounded-card border border-outline shadow-elev-1 px-4 py-3 transition duration-base group-hover:shadow-elev-2 group-hover:-translate-y-0.5 group-focus-visible:ring-2 group-focus-visible:ring-primary motion-reduce:transform-none">
                  <span className="flex items-center justify-between gap-2">
                    <span className="font-display font-bold text-on-surface truncate">
                      {t.name}
                    </span>
                    <span className={`shrink-0 text-[10px] font-bold uppercase px-2 py-0.5 rounded-pill ${d.cls}`}>
                      {d.label}
                    </span>
                  </span>
                  {t.rationale && (
                    <span className="block text-on-surface-muted text-sm mt-0.5 line-clamp-2">
                      {t.rationale}
                    </span>
                  )}
                  {isStart && (
                    <span className="inline-block mt-2 text-primary text-xs font-bold">
                      ▶ Start here
                    </span>
                  )}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
