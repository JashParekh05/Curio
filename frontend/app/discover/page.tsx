"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { getDiscoverFeed, recordClipEvent, type Clip } from "@/lib/api";
import { flushClipEvent, type LastLogged } from "@/lib/clip-telemetry";
import { shareOrCopy, topicShareUrl } from "@/lib/share";
import ReelPlayer from "@/components/ReelPlayer";

export default function DiscoverPage() {
  const router = useRouter();
  const { user, session, loading, isGuest } = useAuth();
  const [clips, setClips] = useState<Clip[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);
  const [fetching, setFetching] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [coldStartTimedOut, setColdStartTimedOut] = useState(false);
  const [readySession, setReadySession] = useState<string | null>(null);
  const [shareToast, setShareToast] = useState<string | null>(null);
  const pollRef = useRef<NodeJS.Timeout | undefined>(undefined);
  const coldStartTimeoutRef = useRef<NodeJS.Timeout | undefined>(undefined);

  const containerRef = useRef<HTMLDivElement>(null);
  const activeIndexRef = useRef(0);
  const clipsRef = useRef<Clip[]>([]);
  const sessionTokenRef = useRef(session?.access_token ?? "");
  const fetchingMoreRef = useRef(false);
  const seenClipIdsRef = useRef<Set<string>>(new Set());
  const isGuestRef = useRef(isGuest);
  const lastLoggedRef = useRef<LastLogged | null>(null);

  activeIndexRef.current = activeIndex;
  clipsRef.current = clips;
  sessionTokenRef.current = session?.access_token ?? "";
  isGuestRef.current = isGuest;

  useEffect(() => {
    if (!user || !session) return;
    const token = session.access_token;

    function doFetch() {
      getDiscoverFeed(user!.id, token).then(({ clips: c, processing }) => {
        const fresh = c.filter((clip) => !seenClipIdsRef.current.has(clip.id));
        fresh.forEach((clip) => seenClipIdsRef.current.add(clip.id));
        if (fresh.length > 0) {
          setClips(fresh);
          setFetching(false);
          clearInterval(pollRef.current);
          clearTimeout(coldStartTimeoutRef.current);
        } else if (!processing) {
          // Library is settled (nothing generating) and still no clips for us —
          // stop polling rather than waiting out the full cold-start window.
          setFetching(false);
          clearInterval(pollRef.current);
          clearTimeout(coldStartTimeoutRef.current);
        }
      }).catch(() => {
        setFetching(false);
        clearInterval(pollRef.current);
      });
    }

    doFetch();
    // Poll every 4s while cold-start seeds are generating.
    // With the backend over-fetch fix, a stocked DB resolves on the first call;
    // this 12s window only covers the genuine cold-start (seeds still generating).
    pollRef.current = setInterval(doFetch, 4000);
    coldStartTimeoutRef.current = setTimeout(() => {
      clearInterval(pollRef.current);
      setFetching(false);
      setColdStartTimedOut(true);
    }, 12000);

    return () => {
      clearInterval(pollRef.current);
      clearTimeout(coldStartTimeoutRef.current);
    };
  }, [user, session]);

  const goTo = useCallback((idx: number) => {
    const clamped = Math.max(0, Math.min(clipsRef.current.length - 1, idx));
    const el = containerRef.current?.querySelectorAll("[data-index]")[clamped] as HTMLElement;
    el?.scrollIntoView({ behavior: "instant" });
  }, []);

  const handleShare = useCallback(async () => {
    const clip = clipsRef.current[activeIndexRef.current];
    if (!clip) return;
    const result = await shareOrCopy(topicShareUrl(clip.topic_slug, clip.id), "Watch this on Curio");
    if (result === "copied" || result === "failed") {
      setShareToast(result === "copied" ? "Link copied" : "Couldn't copy link");
      setTimeout(() => setShareToast(null), 2000);
    }
  }, []);

  // Telemetry — fires on every activeIndex change regardless of input method
  const prevIndexRef = useRef(activeIndex);
  const clipStartRef = useRef<number>(Date.now());
  useEffect(() => {
    const prev = prevIndexRef.current;
    if (prev === activeIndex) return;
    const leavingClip = clipsRef.current[prev];
    if (leavingClip) {
      flushClipEvent({
        clip: leavingClip,
        startedAt: clipStartRef.current,
        sessionId: null,
        replayCount: 0,
        feedback: null,
        token: sessionTokenRef.current,
        keepalive: false,
        isGuest: isGuestRef.current,
        lastLoggedRef,
      });
    }
    prevIndexRef.current = activeIndex;
    clipStartRef.current = Date.now();
  }, [activeIndex]);

  // Flush the CURRENT clip on unmount / tab-close so the last clip is recorded
  // exactly once (the activeIndex effect only logs a clip when leaving it for
  // another index). Mirrors the path-feed page; discover has no session.
  useEffect(() => {
    const flushCurrent = (keepalive: boolean) => {
      flushClipEvent({
        clip: clipsRef.current[activeIndexRef.current],
        startedAt: clipStartRef.current,
        sessionId: null,
        replayCount: 0,
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

  // Auto-load more when 2 from the end
  useEffect(() => {
    if (!user || !session || clips.length === 0 || activeIndex < clips.length - 2) return;
    if (fetchingMoreRef.current) return;
    fetchingMoreRef.current = true;
    getDiscoverFeed(user.id, session.access_token)
      .then((more) => {
        const fresh = more.clips.filter((clip) => !seenClipIdsRef.current.has(clip.id));
        fresh.forEach((clip) => seenClipIdsRef.current.add(clip.id));
        setClips((prev) => [...prev, ...fresh]);
      })
      .finally(() => { fetchingMoreRef.current = false; });
  }, [activeIndex, clips.length, user, session]);

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

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowDown") goTo(activeIndexRef.current + 1);
      if (e.key === "ArrowUp") goTo(activeIndexRef.current - 1);
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [goTo]);

  // Toast: poll localStorage for a completed background path generation
  useEffect(() => {
    const existing = localStorage.getItem("lr_ready_session");
    if (existing) {
      localStorage.removeItem("lr_pending_query");
      localStorage.removeItem("lr_ready_session");
      setReadySession(existing);
      return;
    }
    if (!localStorage.getItem("lr_pending_query")) return;
    const interval = setInterval(() => {
      const sess = localStorage.getItem("lr_ready_session");
      if (sess) {
        clearInterval(interval);
        localStorage.removeItem("lr_pending_query");
        localStorage.removeItem("lr_ready_session");
        setReadySession(sess);
      }
    }, 2000);
    return () => clearInterval(interval);
  }, []);

  if (loading || !user) return null;

  const readyToast = readySession ? (
    <div className="absolute bottom-8 inset-x-4 z-30 flex justify-center">
      <div className="brutal flex items-center gap-3 bg-accent-yellow text-ink px-4 py-3 shadow-brutal">
        <button
          onClick={() => router.push(`/feed?session=${readySession}`)}
          className="text-sm font-bold"
        >
          Your learning path is ready
        </button>
        <button
          onClick={() => setReadySession(null)}
          className="text-ink/60 hover:text-ink text-xs font-black leading-none"
          aria-label="Dismiss"
        >
          X
        </button>
      </div>
    </div>
  ) : null;

  if (fetching) {
    return (
      <div className="fixed inset-0 bg-paper flex flex-col items-center justify-center gap-5 text-ink">
        <button onClick={() => router.push("/")} className="brutal-btn bg-white text-ink absolute top-4 left-4 text-sm px-3 py-2">
          Home
        </button>
        <div className="w-12 h-12 border-[3px] border-ink border-t-accent-pink rounded-full animate-spin" />
        <p className="text-ink/60 text-sm font-bold">Loading your feed</p>
        {readyToast}
      </div>
    );
  }

  if (clips.length === 0) {
    if (coldStartTimedOut) {
      return (
        <div className="fixed inset-0 bg-paper flex flex-col items-center justify-center gap-5 text-ink px-6">
          <button onClick={() => router.push("/")} className="brutal-btn bg-white text-ink absolute top-4 left-4 text-sm px-3 py-2">
            Home
          </button>
          <p className="text-3xl font-black text-center">Nothing to discover yet</p>
          <p className="text-ink/60 text-sm text-center font-medium">Try learning a few topics first — we'll find more content for you.</p>
          <button onClick={() => router.push("/")} className="brutal-btn bg-accent-yellow text-ink px-6 py-3 text-sm">
            Start learning
          </button>
          {readyToast}
        </div>
      );
    }
    return (
      <div className="fixed inset-0 bg-paper flex flex-col items-center justify-center gap-5 text-ink">
        <button onClick={() => router.push("/")} className="brutal-btn bg-white text-ink absolute top-4 left-4 text-sm px-3 py-2">
          Home
        </button>
        <div className="w-12 h-12 border-[3px] border-ink border-t-accent-pink rounded-full animate-spin" />
        <div className="text-center space-y-1">
          <p className="text-ink font-extrabold">Building your feed</p>
          <p className="text-ink/60 text-sm font-medium">Finding clips for your interests</p>
        </div>
        {readyToast}
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
        <span className="brutal-dark bg-accent-orange text-ink text-xs font-bold tracking-wide px-2 py-1">Discover</span>
        <span className="text-white text-xs tabular-nums flex items-center gap-2 pointer-events-auto">
          <span className="brutal-dark bg-ink px-2 py-1 font-bold">{activeIndex + 1} / {clips.length}</span>
          <button
            onClick={handleShare}
            className="brutal-dark-btn bg-accent-cyan text-ink font-bold px-3 py-1.5 text-xs leading-none"
          >
            Share
          </button>
        </span>
      </div>

      {/* Nav arrows */}
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

      {/* Learning path ready toast */}
      {readyToast}

      {shareToast && (
        <div className="absolute bottom-8 inset-x-0 z-40 flex justify-center pointer-events-none">
          <div className="brutal bg-accent-yellow text-ink text-sm font-bold px-4 py-2 shadow-brutal">
            {shareToast}
          </div>
        </div>
      )}

      {/* Progress bar */}
      <div className="absolute top-0 inset-x-0 z-30 h-1 bg-ink">
        <div
          className="h-full bg-accent-lime transition-all duration-300"
          style={{ width: `${((activeIndex + 1) / clips.length) * 100}%` }}
        />
      </div>

      {/* Scroll container */}
      <div ref={containerRef} className="h-full overflow-y-scroll snap-y snap-mandatory" style={{ scrollbarWidth: "none" }}>
        {clips.map((clip, i) => (
          <div key={clip.id} data-index={i} className="w-full relative snap-start snap-always" style={{ height: "100dvh" }}>
            {i === activeIndex ? (
              <ReelPlayer
                clip={clip}
                active={true}
                onEnded={() => goTo(i + 1)}
                onFeedback={(type) => recordClipEvent(clip.id, 0, false, null, 0, type, sessionTokenRef.current)}
              />
            ) : null}
          </div>
        ))}

        {/* End card */}
        <div className="snap-start snap-always" style={{ height: "100dvh" }}>
          <div className="h-full flex flex-col items-center justify-center gap-5 bg-paper text-ink px-6">
            <p className="text-3xl font-black text-center">You&apos;re all caught up</p>
            <p className="text-ink/60 text-sm text-center font-medium">Want to go deeper on something?</p>
            <button
              onClick={() => router.push("/")}
              className="brutal-btn bg-accent-yellow text-ink px-6 py-3 text-sm"
            >
              Learn something specific
            </button>
            <button
              disabled={loadingMore}
              onClick={() => {
                if (!user || !session || loadingMore) return;
                setLoadingMore(true);
                getDiscoverFeed(user.id, session.access_token)
                  .then((more) => {
                    const fresh = more.clips.filter((clip) => !seenClipIdsRef.current.has(clip.id));
                    fresh.forEach((clip) => seenClipIdsRef.current.add(clip.id));
                    setClips((prev) => [...prev, ...fresh]);
                  })
                  .finally(() => setLoadingMore(false));
              }}
              className="brutal-btn bg-white text-ink px-5 py-2.5 text-sm disabled:opacity-40"
            >
              {loadingMore ? "Loading" : "Load more clips"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
