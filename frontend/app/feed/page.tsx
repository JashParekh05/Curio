"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { getPathFeed, getTopicFeed, type Clip, type FeedResponse } from "@/lib/api";
import ReelPlayer from "@/components/ReelPlayer";

const POLL_INTERVAL_MS = 4000;

export default function FeedPage() {
  const params = useSearchParams();
  const router = useRouter();
  const sessionId = params.get("session");
  const topicSlug = params.get("topic");

  const [clips, setClips] = useState<Clip[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);
  const [processing, setProcessing] = useState(false);
  const [topicLabels, setTopicLabels] = useState<Record<string, string>>({});

  const containerRef = useRef<HTMLDivElement>(null);
  const pollingRef = useRef<NodeJS.Timeout>();

  const loadFeed = useCallback(async () => {
    try {
      if (sessionId) {
        const feeds: FeedResponse[] = await getPathFeed(sessionId);
        const allClips = feeds.flatMap((f) => f.clips);
        const labels: Record<string, string> = {};
        feeds.forEach((f) => { labels[f.topic_slug] = f.topic_slug; });
        setClips(allClips);
        setTopicLabels(labels);
        setProcessing(feeds.some((f) => f.processing));
      } else if (topicSlug) {
        const feed = await getTopicFeed(topicSlug);
        setClips(feed.clips);
        setProcessing(feed.processing);
      }
    } catch {
      // silently retry
    }
  }, [sessionId, topicSlug]);

  useEffect(() => {
    loadFeed();
  }, [loadFeed]);

  // Poll while processing
  useEffect(() => {
    if (processing) {
      pollingRef.current = setInterval(loadFeed, POLL_INTERVAL_MS);
    }
    return () => clearInterval(pollingRef.current);
  }, [processing, loadFeed]);

  // Scroll snap: update activeIndex on scroll
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            const idx = Number((entry.target as HTMLElement).dataset.index);
            setActiveIndex(idx);
          }
        });
      },
      { threshold: 0.6, root: container }
    );

    const children = container.querySelectorAll("[data-index]");
    children.forEach((el) => observer.observe(el));
    return () => observer.disconnect();
  }, [clips]);

  if (!sessionId && !topicSlug) {
    return (
      <div className="min-h-screen bg-black text-white flex items-center justify-center">
        <div className="text-center space-y-4">
          <p className="text-zinc-400">No topic selected.</p>
          <button onClick={() => router.push("/")} className="text-white underline">
            Go back
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 bg-black">
      {/* Back button */}
      <button
        onClick={() => router.push("/")}
        className="absolute top-4 left-4 z-20 text-white bg-black/40 backdrop-blur rounded-full px-3 py-1.5 text-sm"
      >
        ← Home
      </button>

      {processing && clips.length === 0 ? (
        <div className="h-full flex flex-col items-center justify-center gap-4 text-white">
          <div className="w-10 h-10 border-2 border-white border-t-transparent rounded-full animate-spin" />
          <p className="text-zinc-400 text-sm">Finding and cutting clips for you…</p>
        </div>
      ) : (
        <div
          ref={containerRef}
          className="h-full overflow-y-scroll snap-y snap-mandatory"
          style={{ scrollbarWidth: "none" }}
        >
          {clips.map((clip, i) => (
            <div
              key={clip.id}
              data-index={i}
              className="h-screen w-full snap-start snap-always relative"
            >
              <ReelPlayer
                clip={clip}
                active={i === activeIndex}
                onEnded={() => {
                  if (i < clips.length - 1) {
                    const next = containerRef.current?.querySelectorAll("[data-index]")[i + 1];
                    next?.scrollIntoView({ behavior: "smooth" });
                  }
                }}
              />
            </div>
          ))}

          {/* Load more trigger */}
          {clips.length > 0 && (
            <div className="h-screen w-full snap-start flex items-center justify-center bg-black">
              <p className="text-zinc-500 text-sm">You've reached the end of this topic.</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
