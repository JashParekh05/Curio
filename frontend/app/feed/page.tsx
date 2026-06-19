"use client";

import { Suspense, useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { getPathFeed, getTopicFeed, recordClipEvent, getRecommendations, type Clip, type FeedResponse, type TopicRecommendation } from "@/lib/api";
import { flushClipEvent, type LastLogged } from "@/lib/clip-telemetry";
import { shareOrCopy, topicShareUrl } from "@/lib/share";
import ReelPlayer from "@/components/ReelPlayer";
import PlanPanel from "@/components/PlanPanel";

const POLL_INTERVAL_MS = 4000;




function FeedContent() {
  const params = useSearchParams();
  const router = useRouter();
  const { session, isGuest } = useAuth();
  const sessionId = params.get("session");
  const topicSlug = params.get("topic");

  const startTopicSlug = params.get("start_topic") ?? null;
  const startSection = params.get("start_section") !== null ? parseInt(params.get("start_section")!) : null;
  const startClipId = params.get("clip");
  const startIndex = Math.max(0, parseInt(params.get("start") ?? "0") || 0);

  const [clips, setClips] = useState<Clip[]>([]);
  const [activeIndex, setActiveIndex] = useState(startIndex);
  const [processing, setProcessing] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [topicLabels, setTopicLabels] = useState<Record<string, string>>({});
  const [timedOut, setTimedOut] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [recommendations, setRecommendations] = useState<TopicRecommendation[]>([]);
  const [showPlan, setShowPlan] = useState(false);
  const [shareToast, setShareToast] = useState<string | null>(null);

  const containerRef = useRef<HTMLDivElement>(null);
  const pollingRef = useRef<NodeJS.Timeout | undefined>(undefined);
  const activeIndexRef = useRef(0);
  const clipsRef = useRef<Clip[]>([]);
  const sessionIdRef = useRef(sessionId);
  const sessionTokenRef = useRef(session?.access_token ?? "");
  const clipStartRef = useRef<number>(Date.now());
  const clipVisitsRef = useRef<Record<string, number>>({});
  const alreadyKnowRef = useRef<Record<string, number>>({});
  const seenClipIdsRef = useRef<Set<string>>(new Set());
  const fetchingMoreRef = useRef(false);
  const isGuestRef = useRef(isGuest);
  const lastLoggedRef = useRef<LastLogged | null>(null);
  const loadFeed = useCallback(async () => {
    if (!session) return;
    try {
      if (sessionId) {
        const feeds: FeedResponse[] = await getPathFeed(sessionId, session?.access_token ?? "");
        const allClips = feeds.flatMap((f) => f.clips);
        const labels: Record<string, string> = {};
        feeds.forEach((f) => {
          f.clips.forEach((c) => { labels[c.id] = f.topic_slug; });
        });
        allClips.forEach((c) => seenClipIdsRef.current.add(c.id));
        if (startTopicSlug) {
          const sectionIdx = startSection !== null
            ? allClips.findIndex((c) => labels[c.id] === startTopicSlug && c.section_index === startSection)
            : -1;
          const topicIdx = allClips.findIndex((c) => labels[c.id] === startTopicSlug);
          const resolved = sectionIdx >= 0 ? sectionIdx : topicIdx;
          if (resolved >= 0) resolvedStartRef.current = resolved;
        }
        setClips((prev) => {
          if (prev.length === 0) return allClips;
          const existingIds = new Set(prev.map((c) => c.id));
          const brandNew = allClips.filter((c) => !existingIds.has(c.id));
          return brandNew.length > 0 ? [...prev, ...brandNew] : prev;
        });
        setTopicLabels((prev) => ({ ...prev, ...labels }));
        setProcessing(feeds.some((f) => f.processing));
        // Terminal-empty short-circuit: when every topic is out of its
        // self-heal budget (failed) or finished empty, and no clips exist at
        // all, stop polling and show the "No clips found" screen immediately
        // instead of waiting for the 5-minute timeout.
        const allTerminal = feeds.length > 0 && feeds.every((f) => f.failed || (!f.processing && f.clips.length === 0));
        if (allClips.length === 0 && allTerminal) {
          clearInterval(pollingRef.current);
          setProcessing(false);
          setTimedOut(true);
        }
        setLoadError(false);
      } else if (topicSlug) {
        const feed = await getTopicFeed(topicSlug, session?.access_token ?? "");
        setClips(feed.clips);
        setProcessing(feed.processing);
        setLoadError(false);
        const labels: Record<string, string> = {};
        feed.clips.forEach((c) => { labels[c.id] = topicSlug; });
        setTopicLabels(labels);
      }
    } catch {
      setLoadError(true);
    } finally {
      setInitialLoading(false);
    }
  }, [sessionId, topicSlug, session]);

  const fetchMore = useCallback(async () => {
    if (!sessionId || fetchingMoreRef.current) return;
    fetchingMoreRef.current = true;
    try {
      const feeds: FeedResponse[] = await getPathFeed(sessionId, session?.access_token ?? "");
      const newClips = feeds.flatMap((f) => f.clips).filter((c) => !seenClipIdsRef.current.has(c.id));
      if (newClips.length === 0) return;
      const newLabels: Record<string, string> = {};
      feeds.forEach((f) => {
        f.clips.forEach((c) => { newLabels[c.id] = f.topic_slug; });
      });
      newClips.forEach((c) => seenClipIdsRef.current.add(c.id));
      setClips((prev) => [...prev, ...newClips]);
      setTopicLabels((prev) => ({ ...prev, ...newLabels }));
    } catch {
      // silently fail — user still has remaining clips
    } finally {
      fetchingMoreRef.current = false;
    }
  }, [sessionId]);

  const initialScrollDoneRef = useRef(false);
  const resolvedStartRef = useRef<number>(startIndex);

  // Restore progress from localStorage (only when no explicit start param)
  useEffect(() => {
    if (!sessionId || startTopicSlug || startIndex > 0) return;
    const saved = localStorage.getItem(`learnreel_progress_${sessionId}`);
    if (saved) resolvedStartRef.current = parseInt(saved, 10) || 0;
  }, [sessionId, startTopicSlug, startIndex]);

  useEffect(() => {
    loadFeed();
    clipStartRef.current = Date.now();
  }, [loadFeed]);

  // Persist progress when activeIndex advances
  useEffect(() => {
    if (!sessionId || activeIndex === 0) return;
    localStorage.setItem(`learnreel_progress_${sessionId}`, String(activeIndex));
  }, [activeIndex, sessionId]);

  // Scroll to resolved start index once clips are available
  useEffect(() => {
    if (initialScrollDoneRef.current || clips.length === 0) return;
    let target = resolvedStartRef.current;
    // A shared "?clip=" deep link lands the visitor on that exact clip.
    if (startClipId) {
      const i = clips.findIndex((c) => c.id === startClipId);
      if (i >= 0) target = i;
    }
    if (target === 0) { initialScrollDoneRef.current = true; return; }
    if (clips.length > target) {
      initialScrollDoneRef.current = true;
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          const el = containerRef.current?.querySelectorAll("[data-index]")[target] as HTMLElement;
          el?.scrollIntoView({ behavior: "instant" });
          setActiveIndex(target);
        });
      });
    }
  }, [clips.length]);

  useEffect(() => {
    if (processing) {
      setTimedOut(false);
      pollingRef.current = setInterval(loadFeed, POLL_INTERVAL_MS);
      // Timer resets whenever new clips arrive (clips.length in deps below),
      // so back-end auto-extend keeps the feed alive past the initial 30s.
      const timeout = setTimeout(() => {
        clearInterval(pollingRef.current);
        // Only show timeout screen if we still have no clips — if clips exist,
        // stop polling silently (user already has content to watch)
        if (clipsRef.current.length === 0) setTimedOut(true);
        setProcessing(false);
      }, 300000);
      return () => { clearInterval(pollingRef.current); clearTimeout(timeout); };
    }
    return () => clearInterval(pollingRef.current);
  }, [processing, clips.length, loadFeed]);

  // Keep refs in sync so goTo/listeners never close over stale values
  activeIndexRef.current = activeIndex;
  clipsRef.current = clips;
  sessionIdRef.current = sessionId;
  sessionTokenRef.current = session?.access_token ?? "";
  isGuestRef.current = isGuest;

  const goTo = useCallback((idx: number) => {
    const clamped = Math.max(0, Math.min(clipsRef.current.length - 1, idx));
    const el = containerRef.current?.querySelectorAll("[data-index]")[clamped] as HTMLElement;
    el?.scrollIntoView({ behavior: "instant" });
  }, []);

  // Single source of truth for telemetry — fires on every activeIndex change regardless of input method
  const prevIndexRef = useRef(activeIndex);
  useEffect(() => {
    const prev = prevIndexRef.current;
    if (prev === activeIndex) return;
    const leavingClip = clipsRef.current[prev];
    if (leavingClip) {
      const visits = clipVisitsRef.current[leavingClip.id] ?? 1;
      flushClipEvent({
        clip: leavingClip,
        startedAt: clipStartRef.current,
        sessionId: sessionIdRef.current,
        replayCount: Math.max(0, visits - 1),
        feedback: null,
        token: sessionTokenRef.current,
        keepalive: false,
        isGuest: isGuestRef.current,
        lastLoggedRef,
      });
    }
    prevIndexRef.current = activeIndex;
    clipStartRef.current = Date.now();
    const arrivingClip = clipsRef.current[activeIndex];
    if (arrivingClip) clipVisitsRef.current[arrivingClip.id] = (clipVisitsRef.current[arrivingClip.id] ?? 0) + 1;
  }, [activeIndex]);

  // Flush the CURRENT clip on unmount / tab-close. The activeIndex effect above
  // only logs a clip when you leave it for another index, so the LAST clip
  // (navigating Home, tapping a recommendation, or closing the tab) would never
  // be recorded. The shared lastLoggedRef dedups against the transition path so
  // the clip is logged exactly once. pagehide + visibilitychange cover tab close
  // and mobile backgrounding; the cleanup covers SPA route changes.
  useEffect(() => {
    const flushCurrent = (keepalive: boolean) => {
      const clip = clipsRef.current[activeIndexRef.current];
      const visits = clip ? (clipVisitsRef.current[clip.id] ?? 1) : 1;
      flushClipEvent({
        clip,
        startedAt: clipStartRef.current,
        sessionId: sessionIdRef.current,
        replayCount: Math.max(0, visits - 1),
        feedback: null,
        token: sessionTokenRef.current,
        keepalive,
        isGuest: isGuestRef.current,
        lastLoggedRef,
      });
    };
    const onPageHide = () => flushCurrent(true);
    const onVisibility = () => { if (document.visibilityState === "hidden") flushCurrent(true); };
    window.addEventListener("pagehide", onPageHide);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("pagehide", onPageHide);
      document.removeEventListener("visibilitychange", onVisibility);
      flushCurrent(true);
    };
  }, []);

  // Stable IntersectionObserver — created once, re-observes new clips as count grows
  const observerRef = useRef<IntersectionObserver | null>(null);
  useEffect(() => {
    observerRef.current = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting && entry.intersectionRatio >= 0.6) {
            const idx = parseInt((entry.target as HTMLElement).dataset.index ?? "-1");
            if (idx >= 0 && idx !== activeIndexRef.current) setActiveIndex(idx);
          }
        }
      },
      { root: containerRef.current, threshold: 0.6 }
    );
    return () => observerRef.current?.disconnect();
  }, []);
  useEffect(() => {
    containerRef.current?.querySelectorAll("[data-index]").forEach((el) => observerRef.current?.observe(el));
  }, [clips.length]);

  // Keyboard navigation
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowDown") goTo(activeIndexRef.current + 1);
      if (e.key === "ArrowUp") goTo(activeIndexRef.current - 1);
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [goTo]);

  // Fetch more clips when 2 from the end (uses updated interest vector)
  useEffect(() => {
    if (sessionId && clips.length > 0 && activeIndex >= clips.length - 2) {
      fetchMore();
    }
  }, [activeIndex, clips.length, sessionId, fetchMore]);

  // Fetch recommendations when user reaches the last clip
  useEffect(() => {
    if (!sessionId || clips.length === 0 || activeIndex < clips.length - 1) return;
    getRecommendations(sessionId, session?.access_token ?? "").then(setRecommendations).catch(() => {});
  }, [activeIndex, clips.length, sessionId]);

  // Derive current topic name from active clip
  const activeClip = clips[activeIndex];
  const activeTopicSlug = activeClip ? (topicLabels[activeClip.id] ?? topicSlug ?? "") : "";
  const activeTopicName = activeTopicSlug
    .split("-")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");

  // Ordered, de-duplicated topics for the plan overlay, in feed order.
  const orderedTopics = useMemo(() => {
    const seen = new Set<string>();
    const out: { slug: string; name: string }[] = [];
    for (const c of clips) {
      const slug = topicLabels[c.id];
      if (slug && !seen.has(slug)) {
        seen.add(slug);
        out.push({
          slug,
          name: slug.split("-").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" "),
        });
      }
    }
    return out;
  }, [clips, topicLabels]);

  // Jump to a topic (or a specific section) from the plan overlay. Prefer an
  // in-place scroll to the already-loaded clip; fall back to a route navigation
  // with start params when that beat hasn't been fetched yet.
  const jumpToPlan = useCallback((slug: string, sectionIndex: number | null) => {
    const idx = clips.findIndex(
      (c) => topicLabels[c.id] === slug && (sectionIndex === null || c.section_index === sectionIndex),
    );
    if (idx >= 0) {
      goTo(idx);
    } else if (sessionId) {
      const extra = sectionIndex !== null
        ? `&start_topic=${slug}&start_section=${sectionIndex}`
        : `&start_topic=${slug}`;
      router.push(`/feed?session=${sessionId}${extra}`);
    }
    setShowPlan(false);
  }, [clips, topicLabels, goTo, sessionId, router]);

  // Share the current topic (deep link lands a new visitor straight on it).
  const handleShare = useCallback(async () => {
    if (!activeTopicSlug) return;
    const result = await shareOrCopy(
      topicShareUrl(activeTopicSlug, activeClip?.id),
      `Learn ${activeTopicName} on Curio`,
    );
    if (result === "copied") {
      setShareToast("Link copied");
      setTimeout(() => setShareToast(null), 2000);
    } else if (result === "failed") {
      setShareToast("Couldn't copy link");
      setTimeout(() => setShareToast(null), 2000);
    }
  }, [activeTopicSlug, activeTopicName, activeClip]);

  if (initialLoading && clips.length === 0) {
    return (
      <div className="fixed inset-0 bg-paper flex items-center justify-center">
        <div className="w-12 h-12 border-[3px] border-ink border-t-accent-pink rounded-full animate-spin" />
      </div>
    );
  }

  if (!sessionId && !topicSlug) {
    return (
      <div className="min-h-screen bg-paper text-ink flex items-center justify-center">
        <div className="text-center space-y-4">
          <p className="text-ink font-bold">No topic selected.</p>
          <button onClick={() => router.push("/")} className="brutal-btn bg-accent-yellow text-ink px-6 py-3">Go back</button>
        </div>
      </div>
    );
  }

  // Network/load error with no clips
  if (loadError && clips.length === 0) {
    return (
      <div className="fixed inset-0 bg-paper flex flex-col items-center justify-center gap-5 text-ink px-6">
        <button onClick={() => router.push("/")} className="brutal-btn bg-white text-ink absolute top-4 left-4 text-sm px-3 py-2">
          Home
        </button>
        <p className="text-3xl font-black text-center">Couldn't load clips</p>
        <p className="text-ink/60 text-sm text-center font-medium">Check that the backend is running.</p>
        <button
          onClick={() => { setLoadError(false); loadFeed(); }}
          className="brutal-btn bg-accent-yellow text-ink px-6 py-3 text-sm"
        >
          Retry
        </button>
      </div>
    );
  }

  // No clips and timed out
  if (timedOut && clips.length === 0) {
    return (
      <div className="fixed inset-0 bg-paper flex flex-col items-center justify-center gap-5 text-ink px-6">
        <button
          onClick={() => router.push("/")}
          className="brutal-btn bg-white text-ink absolute top-4 left-4 text-sm px-3 py-2"
        >
          Home
        </button>
        <p className="text-3xl font-black text-center">No clips found</p>
        <p className="text-ink/60 text-sm text-center font-medium">Try a different topic — we may not have content for this one yet.</p>
        <button
          onClick={() => router.push("/")}
          className="brutal-btn bg-accent-yellow text-ink px-6 py-3 text-sm"
        >
          Try another topic
        </button>
      </div>
    );
  }

  // Pure loading (no clips at all yet)
  if (processing && clips.length === 0) {
    return (
      <div className="fixed inset-0 bg-paper flex flex-col items-center justify-center gap-5 text-ink">
        <button
          onClick={() => router.push("/")}
          className="brutal-btn bg-white text-ink absolute top-4 left-4 text-sm px-3 py-2"
        >
          Home
        </button>
        <div className="w-12 h-12 border-[3px] border-ink border-t-accent-pink rounded-full animate-spin" />
        <div className="text-center space-y-1">
          <p className="text-ink font-extrabold">Finding clips for you</p>
          <p className="text-ink/60 text-sm font-medium">Hang tight</p>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 bg-black">
      {/* HUD */}
      <div className="absolute top-0 inset-x-0 z-20 flex items-center justify-between px-4 pt-4 pb-2 pointer-events-none">
        <button
          onClick={() => router.push("/")}
          className="pointer-events-auto brutal-dark-btn bg-ink text-white font-bold px-3 py-1.5 text-sm leading-none"
        >
          Home
        </button>

        {activeTopicName && (
          <span className="brutal-dark bg-accent-purple text-white text-xs font-bold tracking-wide px-2 py-1 max-w-[40%] truncate">
            {activeTopicName}
          </span>
        )}

        <span className="text-white text-xs tabular-nums flex items-center gap-2 pointer-events-auto">
          {clips.length > 0 ? (
            <span className="brutal-dark bg-ink px-2 py-1 font-bold">{activeIndex + 1} / {clips.length}</span>
          ) : ""}
          {activeTopicSlug && (
            <button
              onClick={handleShare}
              className="brutal-dark-btn bg-accent-cyan text-ink font-bold px-3 py-1.5 text-xs leading-none"
            >
              Share
            </button>
          )}
          {sessionId && orderedTopics.length > 0 && (
            <button
              onClick={() => setShowPlan(true)}
              className="brutal-dark-btn bg-accent-yellow text-ink font-bold px-3 py-1.5 text-xs leading-none"
            >
              Plan
            </button>
          )}
        </span>
      </div>

      {sessionId && (
        <PlanPanel
          open={showPlan}
          onClose={() => setShowPlan(false)}
          topics={orderedTopics}
          activeSlug={activeTopicSlug}
          sessionId={sessionId}
          onJump={jumpToPlan}
        />
      )}

      {shareToast && (
        <div className="absolute bottom-8 inset-x-0 z-40 flex justify-center pointer-events-none">
          <div className="brutal bg-accent-yellow text-ink text-sm font-bold px-4 py-2 shadow-brutal">
            {shareToast}
          </div>
        </div>
      )}

      {/* Nav arrows */}
      {clips.length > 0 && (
        <div className="absolute left-3 top-1/2 -translate-y-1/2 z-20 flex flex-col gap-2">
          <button
            onClick={() => goTo(activeIndex - 1)}
            disabled={activeIndex === 0}
            aria-label="Previous clip"
            className="brutal-dark-btn bg-ink w-9 h-9 flex items-center justify-center text-white font-black disabled:opacity-20 disabled:translate-x-0 disabled:translate-y-0 disabled:shadow-brutal-white"
          >
            ^
          </button>
          <button
            onClick={() => goTo(activeIndex + 1)}
            disabled={activeIndex >= clips.length - 1}
            aria-label="Next clip"
            className="brutal-dark-btn bg-ink w-9 h-9 flex items-center justify-center text-white font-black disabled:opacity-20 disabled:translate-x-0 disabled:translate-y-0 disabled:shadow-brutal-white"
          >
            v
          </button>
        </div>
      )}

      {/* Progress bar */}
      {clips.length > 0 && (
        <div className="absolute top-0 inset-x-0 z-30 h-1 bg-ink">
          <div
            className="h-full bg-accent-lime transition-all duration-300"
            style={{ width: `${((activeIndex + 1) / clips.length) * 100}%` }}
          />
        </div>
      )}

      {/* Clip scroll container */}
      <div
        ref={containerRef}
        className="h-full overflow-y-scroll snap-y snap-mandatory"
        style={{ scrollbarWidth: "none" }}
      >
        {clips.map((clip, i) => (
          <div
            key={clip.id}
            data-index={i}
            className="w-full relative snap-start snap-always"
            style={{ height: "100dvh" }}
          >
            {i === activeIndex ? (
              <ReelPlayer
                clip={clip}
                active={true}
                onEnded={() => goTo(i + 1)}
                onFeedback={sessionId ? (type) => {
                  recordClipEvent(clip.id, Date.now() - clipStartRef.current, false, sessionId, 0, type, session?.access_token ?? "");
                  if (type === "already_know") {
                    // Skip the current clip and stay within the topic. After a few
                    // ✓'s in the same topic, skip past the whole topic. Navigate by
                    // index only — mutating the clips array corrupts the snap scroll.
                    const topic = topicLabels[clip.id];
                    alreadyKnowRef.current[topic] = (alreadyKnowRef.current[topic] ?? 0) + 1;
                    if (alreadyKnowRef.current[topic] >= 3) {
                      const nextTopicIdx = clips.findIndex((c, idx) => idx > i && topicLabels[c.id] !== topic);
                      goTo(nextTopicIdx === -1 ? clips.length - 1 : nextTopicIdx);
                    } else {
                      goTo(i + 1);
                    }
                  }
                } : undefined}
              />
            ) : null}
          </div>
        ))}

        {/* End card */}
        {clips.length > 0 && !processing && (
          <div className="snap-start snap-always" style={{ height: "100dvh" }}>
            <div className="h-full flex flex-col items-center justify-center gap-5 bg-paper text-ink px-6">
              <p className="text-3xl font-black text-center">You finished this topic.</p>
              <p className="text-ink/60 text-sm text-center font-medium">
                You watched {clips.length} clip{clips.length !== 1 ? "s" : ""}.
              </p>
              {recommendations.length > 0 ? (
                <>
                  <p className="text-ink text-xs font-black uppercase tracking-wide">What to learn next</p>
                  <div className="w-full max-w-sm space-y-3">
                    {recommendations.map((rec, ri) => (
                      <button
                        key={rec.slug}
                        onClick={() => router.push(`/feed?topic=${rec.slug}`)}
                        className={`brutal-btn w-full text-left ${["bg-accent-yellow","bg-accent-cyan","bg-accent-lime","bg-accent-pink"][ri % 4]} text-ink px-4 py-3`}
                      >
                        <p className="font-bold text-sm">{rec.name}</p>
                        <p className="text-ink/60 text-xs mt-0.5 font-medium">{rec.clip_count} clips · {rec.difficulty}</p>
                      </button>
                    ))}
                  </div>
                </>
              ) : (
                <button
                  onClick={() => router.push("/")}
                  className="brutal-btn bg-accent-yellow text-ink px-6 py-3 text-sm"
                >
                  Learn something new
                </button>
              )}
            </div>
          </div>
        )}

        {/* Still loading more */}
        {clips.length > 0 && processing && (
          <div className="snap-start snap-always" style={{ height: "100dvh" }}>
            <div className="h-full flex flex-col items-center justify-center gap-4 bg-paper text-ink">
              <div className="w-8 h-8 border-[3px] border-ink border-t-accent-pink rounded-full animate-spin" />
              <p className="text-ink/60 text-sm font-bold">Loading more clips</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function FeedPage() {
  return (
    <Suspense
      fallback={
        <div className="fixed inset-0 bg-paper flex items-center justify-center">
          <div className="w-12 h-12 border-[3px] border-ink border-t-accent-pink rounded-full animate-spin" />
        </div>
      }
    >
      <FeedContent />
    </Suspense>
  );
}
