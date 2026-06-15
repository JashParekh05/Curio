"use client";

import { useState } from "react";
import { useAuth } from "@/lib/auth-context";
import { getTopicSections, type TopicSection } from "@/lib/api";

export interface PlanTopic {
  slug: string;
  name: string;
}

/**
 * Slide-in overlay of the learning plan while watching the feed. Tapping a
 * topic jumps to its first clip; expanding shows its sections (lazy-loaded) and
 * tapping one jumps straight to that beat. Mirrors the home-page plan tree.
 */
export default function PlanPanel({
  open,
  onClose,
  topics,
  activeSlug,
  onJump,
}: {
  open: boolean;
  onClose: () => void;
  topics: PlanTopic[];
  activeSlug: string;
  onJump: (slug: string, sectionIndex: number | null) => void;
}) {
  const { session } = useAuth();
  const [expanded, setExpanded] = useState<string | null>(null);
  const [sections, setSections] = useState<Record<string, TopicSection[]>>({});
  const [loading, setLoading] = useState<string | null>(null);

  async function toggle(slug: string) {
    if (expanded === slug) {
      setExpanded(null);
      return;
    }
    setExpanded(slug);
    if (!sections[slug] && session) {
      setLoading(slug);
      try {
        const secs = await getTopicSections(slug, session.access_token);
        setSections((prev) => ({ ...prev, [slug]: secs }));
      } catch {
        /* leave empty — shows "no sections yet" */
      }
      setLoading(null);
    }
  }

  if (!open) return null;

  return (
    <div className="absolute inset-0 z-40 flex">
      <div className="flex-1 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div
        className="w-[85%] max-w-sm h-full bg-zinc-950 border-l border-zinc-800 overflow-y-auto"
        style={{ scrollbarWidth: "none" }}
      >
        <div className="sticky top-0 bg-zinc-950/95 backdrop-blur px-4 py-3 flex items-center justify-between border-b border-zinc-800">
          <p className="text-white font-semibold text-sm">Your plan</p>
          <button onClick={onClose} className="text-zinc-500 hover:text-white text-sm leading-none" aria-label="Close plan">
            ✕
          </button>
        </div>

        <div className="p-3 space-y-2">
          {topics.map((topic, i) => {
            const isActive = topic.slug === activeSlug;
            return (
              <div
                key={topic.slug}
                className={`rounded-xl overflow-hidden border ${isActive ? "border-white/40" : "border-zinc-800"}`}
              >
                <div className="flex items-center gap-2 bg-zinc-900/60 px-3 py-2.5">
                  <span className="text-zinc-600 text-xs w-4 shrink-0">{i + 1}</span>
                  <button className="flex-1 min-w-0 text-left" onClick={() => onJump(topic.slug, null)}>
                    <p className={`text-sm font-medium truncate ${isActive ? "text-white" : "text-zinc-300"}`}>
                      {topic.name}
                    </p>
                  </button>
                  <button
                    onClick={() => toggle(topic.slug)}
                    className="text-zinc-500 hover:text-white text-xs px-1.5 py-1"
                    aria-label="Show sections"
                  >
                    {expanded === topic.slug ? "▲" : "▼"}
                  </button>
                </div>

                {expanded === topic.slug && (
                  <div className="bg-zinc-900/40 divide-y divide-zinc-800/60">
                    {loading === topic.slug ? (
                      <div className="px-4 py-2.5 text-zinc-600 text-xs">Loading…</div>
                    ) : sections[topic.slug]?.length ? (
                      sections[topic.slug].map((sec) => (
                        <button
                          key={sec.section_index}
                          onClick={() => onJump(topic.slug, sec.section_index)}
                          className="w-full flex items-center gap-2 text-left px-4 py-2 hover:bg-zinc-800/60 active:scale-[0.99] transition"
                        >
                          <span className="text-zinc-600 text-xs w-3 shrink-0">{sec.section_index + 1}</span>
                          <p className="text-zinc-400 text-xs flex-1 min-w-0 truncate">{sec.title}</p>
                          <span className="text-zinc-600 text-xs shrink-0">▶</span>
                        </button>
                      ))
                    ) : (
                      <div className="px-4 py-2.5 text-zinc-600 text-xs">No sections yet.</div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
