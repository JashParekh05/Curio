import { describe, it, expect } from "vitest";
import fc from "fast-check";
import {
  createOverlayCache,
  getOverlay,
  setOverlay,
  hasOverlay,
  type OverlayMetadata,
} from "./overlay-cache";

/** Arbitrary OverlayMetadata. */
const overlayArb: fc.Arbitrary<OverlayMetadata> = fc.record({
  title: fc.string(),
  description: fc.option(fc.string(), { nil: null }),
  topicLabel: fc.string(),
});

/** Small id pool to force overwrites of the same key. */
const idArb = fc.constantFrom("a", "b", "c");

describe("OverlayCache", () => {
  it("get-after-set returns the last written value per id", () => {
    // Feature: feed-prefetch-playback, Property 3: get-after-set round-trip -
    // getOverlay returns the most recently stored value for every touched id, and
    // reports a miss (undefined / hasOverlay false) for an untouched id.
    fc.assert(
      fc.property(
        fc.array(fc.tuple(idArb, overlayArb), { minLength: 1, maxLength: 30 }),
        (commands) => {
          const cache = createOverlayCache();
          const oracle = new Map<string, OverlayMetadata>();

          for (const [id, meta] of commands) {
            setOverlay(cache, id, meta);
            oracle.set(id, meta);
          }

          // every touched id deep-equals the last-written value
          for (const [id, expected] of oracle.entries()) {
            expect(hasOverlay(cache, id)).toBe(true);
            expect(getOverlay(cache, id)).toEqual(expected);
          }

          // an untouched id reports a miss
          const untouched = "untouched-id";
          if (!oracle.has(untouched)) {
            expect(hasOverlay(cache, untouched)).toBe(false);
            expect(getOverlay(cache, untouched)).toBeUndefined();
          }
        },
      ),
      { numRuns: 100 },
    );
  });

  it("storing the same value repeatedly is idempotent", () => {
    // Feature: feed-prefetch-playback, Property 4: idempotent store - applying
    // setOverlay repeatedly with the same (id, value) leaves the cache holding a
    // value deep-equal to the value after the first store.
    fc.assert(
      fc.property(
        idArb,
        overlayArb,
        fc.integer({ min: 1, max: 5 }),
        (id, meta, repeats) => {
          const cache = createOverlayCache();

          setOverlay(cache, id, meta);
          const afterFirst = getOverlay(cache, id);

          for (let i = 0; i < repeats; i++) {
            setOverlay(cache, id, meta);
          }

          expect(getOverlay(cache, id)).toEqual(afterFirst);
          expect(getOverlay(cache, id)).toEqual(meta);
        },
      ),
      { numRuns: 100 },
    );
  });
});
