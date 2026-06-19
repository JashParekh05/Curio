import { describe, it, expect } from "vitest";
import fc from "fast-check";
import {
  isRefreshEligible,
  type RefreshEligibilityInput,
} from "./refresh-eligibility";

describe("isRefreshEligible", () => {
  it("matches the reference predicate and is deterministic", () => {
    // Feature: feed-prefetch-playback, Property 5: deterministic predicate -
    // isRefreshEligible equals hasActiveClip && !inFlight && !(lastRefreshAt !== null
    // && now - lastRefreshAt < minIntervalMs), and is referentially transparent
    // (same input -> same output across repeated calls).
    fc.assert(
      fc.property(
        fc.boolean(), // hasActiveClip
        fc.boolean(), // inFlight
        fc.option(fc.nat(), { nil: null }), // lastRefreshAt
        fc.nat(), // now
        fc.nat(), // minIntervalMs
        (hasActiveClip, inFlight, lastRefreshAt, now, minIntervalMs) => {
          const input: RefreshEligibilityInput = {
            hasActiveClip,
            inFlight,
            lastRefreshAt,
            now,
            minIntervalMs,
          };

          const reference =
            hasActiveClip &&
            !inFlight &&
            !(
              lastRefreshAt !== null &&
              now - lastRefreshAt < minIntervalMs
            );

          const actual = isRefreshEligible(input);
          expect(actual).toBe(reference);

          // determinism: a second call with the same input returns the same value
          expect(isRefreshEligible(input)).toBe(actual);
        },
      ),
      { numRuns: 100 },
    );
  });
});
