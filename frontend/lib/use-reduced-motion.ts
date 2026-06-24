"use client";

import { useEffect, useState } from "react";

// SSR-safe `prefers-reduced-motion` hook for the new design system's non-Framer
// contexts (CSS-driven transitions, count-ups, celebration bursts). Returns
// false on the server and until mounted (so markup matches), then tracks the
// live OS preference. Components that use Framer Motion should prefer its own
// `useReducedMotion()`; this is for everything else.
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
      return;
    }
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => setReduced(mq.matches);
    apply();
    mq.addEventListener?.("change", apply);
    return () => mq.removeEventListener?.("change", apply);
  }, []);
  return reduced;
}
