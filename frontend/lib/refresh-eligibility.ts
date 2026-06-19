// Pure core: refresh-eligibility evaluator for refresh-on-return.
//
// No React, DOM, or clock reads -- the clock is injected via `now`, so the
// function is deterministic in its inputs (same input -> same output). This
// keeps the correctness-critical "should this Return_Event trigger a
// Single_Clip_Refresh?" decision unit- and property-testable in isolation,
// mirroring the pure-decision-core convention used elsewhere in the codebase.

export interface RefreshEligibilityInput {
  /** false when there is no Active_Clip (R8.4). */
  hasActiveClip: boolean;
  /** true when a Single_Clip_Refresh is already running (R8.2). */
  inFlight: boolean;
  /** epoch ms of the last completed refresh; null if never refreshed. */
  lastRefreshAt: number | null;
  /** injected clock (epoch ms) -- keeps the function pure (R8.5). */
  now: number;
  /** configured minimum interval between refreshes, in ms (R8.3). */
  minIntervalMs: number;
}

/**
 * Decide whether a Return_Event should trigger a Single_Clip_Refresh. Pure:
 * computed solely from inputs, identical output for identical input (R8.5, R12.5).
 *
 *   if !hasActiveClip  -> false                                     (R8.4)
 *   if inFlight        -> false                                     (R8.2)
 *   if lastRefreshAt !== null
 *      && (now - lastRefreshAt) < minIntervalMs -> false            (R8.3)
 *   otherwise          -> true                                      (R8.1)
 *
 * lastRefreshAt === null means "never refreshed" -> the interval gate passes.
 */
export function isRefreshEligible(input: RefreshEligibilityInput): boolean {
  const { hasActiveClip, inFlight, lastRefreshAt, now, minIntervalMs } = input;

  if (!hasActiveClip) return false; // R8.4
  if (inFlight) return false; // R8.2
  if (lastRefreshAt !== null && now - lastRefreshAt < minIntervalMs) return false; // R8.3

  return true; // R8.1
}

/** Minimum interval between refreshes of the same Active_Clip (Constants/config). */
export const REFRESH_MIN_INTERVAL_MS = 30_000;
