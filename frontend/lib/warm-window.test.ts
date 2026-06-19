import { describe, it, expect } from "vitest";
import fc from "fast-check";
import { computeWarmWindow } from "./warm-window";

/** Reference clamp matching the pure core's internal clamp semantics. */
function clamp(value: number, min: number, max: number): number {
  if (value < min) return min;
  if (value > max) return max;
  return value;
}

describe("computeWarmWindow", () => {
  it("returns a valid, bounded window for any input", () => {
    // Feature: feed-prefetch-playback, Property 1: Warm_Window is valid & bounded -
    // every index is within [0, clipCount-1], unique, the window size never exceeds
    // ahead + behind + 1, and it is empty exactly when clipCount === 0.
    fc.assert(
      fc.property(
        fc.integer(), // activeIndex (incl. negatives / out-of-range)
        fc.nat(), // clipCount (incl. 0)
        fc.nat({ max: 5 }), // ahead
        fc.nat({ max: 5 }), // behind
        (activeIndex, clipCount, ahead, behind) => {
          const w = computeWarmWindow(activeIndex, clipCount, ahead, behind);

          // empty exactly when there are no clips
          if (clipCount === 0) {
            expect(w).toEqual([]);
            return;
          }

          // every element within [0, clipCount - 1]
          for (const idx of w) {
            expect(idx).toBeGreaterThanOrEqual(0);
            expect(idx).toBeLessThanOrEqual(clipCount - 1);
          }

          // no duplicates
          expect(new Set(w).size).toBe(w.length);

          // bounded size
          expect(w.length).toBeLessThanOrEqual(ahead + behind + 1);

          // min/max within range when non-empty
          if (w.length > 0) {
            expect(Math.min(...w)).toBeGreaterThanOrEqual(0);
            expect(Math.max(...w)).toBeLessThanOrEqual(clipCount - 1);
          }
        },
      ),
      { numRuns: 100 },
    );
  });

  it("always includes the clamped active index when there is at least one clip", () => {
    // Feature: feed-prefetch-playback, Property 2: Warm_Window includes the active
    // clip - the window always contains clamp(activeIndex, 0, clipCount-1).
    fc.assert(
      fc.property(
        fc.integer({ min: 1 }), // clipCount >= 1
        fc.integer(), // activeIndex (incl. negatives / out-of-range)
        fc.nat({ max: 5 }), // ahead
        fc.nat({ max: 5 }), // behind
        (clipCount, activeIndex, ahead, behind) => {
          const w = computeWarmWindow(activeIndex, clipCount, ahead, behind);
          const clampedActive = clamp(activeIndex, 0, clipCount - 1);
          expect(w.includes(clampedActive)).toBe(true);
        },
      ),
      { numRuns: 100 },
    );
  });
});
