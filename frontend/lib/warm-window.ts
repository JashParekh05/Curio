/**
 * Pure warm-window selector for the clip feed.
 *
 * This module is PURE: it imports nothing, reads no React/DOM/network/clock state,
 * and mutates no module-global. Every export is a deterministic function of its
 * inputs, which makes it unit- and property-testable in isolation.
 *
 * `computeWarmWindow` returns the contiguous, ascending set of clip indices to mount
 * as players around the active index. Guarantees (validated by property tests):
 *   - every returned index is in the inclusive range [0, clipCount - 1]
 *   - no duplicate indices (contiguous range built from two clamped endpoints)
 *   - size is at most `ahead + behind + 1`
 *   - includes clamp(activeIndex, 0, clipCount - 1) whenever clipCount >= 1
 *   - returns [] when clipCount <= 0
 *
 * Negative `ahead`/`behind` are treated as 0 (the endpoint clamps absorb them), so
 * the function is total for any numeric input.
 */

/** Clamp `value` to the inclusive range [min, max]. */
function clamp(value: number, min: number, max: number): number {
  if (value < min) return min;
  if (value > max) return max;
  return value;
}

/**
 * Compute the warm window: the contiguous set of clip indices around `activeIndex`.
 *
 * Algorithm:
 *   if clipCount <= 0           -> []
 *   clampedActive = clamp(activeIndex, 0, clipCount - 1)
 *   lo = clamp(clampedActive - behind, 0, clipCount - 1)
 *   hi = clamp(clampedActive + ahead,  0, clipCount - 1)
 *   -> [lo, lo + 1, ..., hi]   (ascending, unique and in-range by construction)
 */
export function computeWarmWindow(
  activeIndex: number,
  clipCount: number,
  ahead: number,
  behind: number,
): number[] {
  if (clipCount <= 0) return [];

  const maxIndex = clipCount - 1;
  const clampedActive = clamp(activeIndex, 0, maxIndex);
  const lo = clamp(clampedActive - behind, 0, maxIndex);
  const hi = clamp(clampedActive + ahead, 0, maxIndex);

  const window: number[] = [];
  for (let i = lo; i <= hi; i++) {
    window.push(i);
  }
  return window;
}

/** Default count of indices warmed after the active index. */
export const AHEAD = 1;

/** Default count of indices warmed before the active index. */
export const BEHIND = 1;
