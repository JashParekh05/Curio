# Bugfix Requirements Document

## Introduction

When a user enters a topic in the path feed, the loading spinner can spin forever and no clips ever appear. Production logs show the same topics reporting `0 clips after segmentation` and `stored 0/0 clips`, immediately followed by `[feed] self-heal: triggered generation for empty topic=...` repeating every ~4s poll cycle, indefinitely.

The two paid dependencies (TranscriptAPI for transcripts, OpenAI for segmentation) were independently confirmed healthy via their dashboards and are explicitly NOT the cause. The failure is code-level and has three parts:

1. **Runaway self-heal loop (primary).** In `get_path_feed` (`backend/app/api/feed.py`), a topic with no clips that is not currently in `generating_slugs` is re-queued for background generation on every poll. The `generating_slugs` guard (`backend/app/api/topics.py`) only de-dupes concurrent runs and is discarded in a `finally` block when generation ends. So a topic that finishes with 0 clips is immediately eligible again on the next 4s poll. There is no cooldown and no attempt cap. Each trigger runs ~4 YouTube Data API searches at 100 quota units each against a 10,000 units/day free quota (~100 searches), so the loop can exhaust the entire daily quota in under a minute. After that, every search returns empty/403, every topic yields 0 clips, and the loop re-fires forever.

2. **No terminal failed state (secondary).** A topic with no clips reports `processing: true` indefinitely (`FeedResponse(processing=is_generating or len(clips) == 0)`). The frontend (`frontend/app/feed/page.tsx`) keeps polling and showing the spinner with no way to stop except a 5-minute client timeout. There is no "this topic failed to generate" state surfaced to the user.

3. **Silent segmentation zero-out (contributing).** In `_node_segment` (`backend/app/agents/pipeline_agent.py`), if `_identify_segments` (`backend/app/services/pipeline.py`) returns an empty list (LLM error or unparseable JSON, both of which log and return `[]`), that video contributes zero clips with no fallback. A transient segmentation failure silently produces 0 clips rather than degrading gracefully.

The fix must stop the unbounded retries (protect YouTube quota with a cooldown and/or max-attempts cap), surface a terminal failed/empty state so the UI can stop spinning, and make the reason for "0 clips" observable. All changes must respect the codebase conventions: best-effort everywhere (never block clip delivery), pure/testable seams, FakeDB-based tests, ASCII only, low latency.

## Bug Analysis

### Current Behavior (Defect)

What currently happens when the bug is triggered:

1.1 WHEN a path-feed topic has no clips and is not in `generating_slugs` THEN the system re-queues background generation for that topic on every poll cycle, with no cooldown and no attempt cap.

1.2 WHEN a topic completes a generation attempt that produces 0 clips THEN the system removes it from `generating_slugs` in a `finally` block, making it immediately eligible for re-triggering on the next ~4s poll.

1.3 WHEN repeated self-heal triggers fire THEN the system consumes ~400 YouTube Data API quota units per attempt (about 4 searches at 100 units each) and exhausts the 10,000 unit/day quota within roughly a minute, after which all subsequent searches return empty or 403.

1.4 WHEN a topic has no clips and cannot produce any (quota exhausted, repeated empties, or no viable videos) THEN the system reports `processing: true` for that topic indefinitely, so the frontend keeps polling and shows the loading spinner with no terminal outcome.

1.5 WHEN `_identify_segments` returns an empty list for a video (LLM call error or unparseable JSON) THEN the system contributes zero clips for that video with no fallback clip, silently yielding 0 clips for an otherwise-viable video.

1.6 WHEN a topic produces 0 clips THEN the system does not distinguish whether the cause was an empty/quota-blocked YouTube search versus an empty segmentation result, so the failure reason is not observable in logs or state.

### Expected Behavior (Correct)

What should happen instead:

2.1 WHEN a path-feed topic has no clips and is not currently generating THEN the system SHALL trigger background generation only if that topic is within its retry budget (within the max-attempts cap AND past the per-slug cooldown window since its last attempt), and SHALL otherwise skip the trigger.

2.2 WHEN a topic completes a generation attempt that produces 0 clips THEN the system SHALL record that a failed/empty attempt occurred (attempt count and last-attempt time) so the cooldown and max-attempts cap survive past the `generating_slugs` lifecycle and prevent immediate re-triggering on the next poll.

2.3 WHEN a topic has reached the max-attempts cap (or its last attempt failed and the cooldown has not elapsed) THEN the system SHALL NOT issue any further YouTube searches for that topic, bounding total quota spend per topic.

2.4 WHEN a topic has exhausted its generation attempts and still has no clips THEN the system SHALL report a terminal non-processing state for that topic (`processing: false` with a failed/empty-terminal signal) so the frontend can stop polling and show a useful message instead of spinning forever.

2.5 WHEN `_identify_segments` returns an empty list for a video that has a transcript THEN the system SHALL fall back to contributing at least one clip for that video (degrading gracefully) rather than silently producing zero clips.

2.6 WHEN a topic produces 0 clips THEN the system SHALL record/log a distinct reason (for example: search returned no videos / quota or 403 response, versus segmentation returned empty) so the cause is observable.

### Unchanged Behavior (Regression Prevention)

Existing behavior that must be preserved:

3.1 WHEN a path-feed topic already has clips THEN the system SHALL CONTINUE TO return those clips, scored, ranked, and interleaved exactly as before.

3.2 WHEN a topic is actively generating (its slug is in `generating_slugs`) THEN the system SHALL CONTINUE TO report `processing: true` and SHALL NOT issue a duplicate concurrent generation trigger.

3.3 WHEN a topic has no clips but is still within its retry budget (under the attempt cap and past any cooldown) THEN the system SHALL CONTINUE TO self-heal by triggering background generation, so transient losses (server restart / OOM) still recover automatically.

3.4 WHEN a generation attempt produces one or more clips THEN the system SHALL CONTINUE TO store and deliver them, and SHALL clear any failed/empty attempt tracking for that topic so the topic behaves as a normal populated topic.

3.5 WHEN a video's transcript is successfully segmented into one or more clips THEN the system SHALL CONTINUE TO produce those clips unchanged, with no extra fallback clip added.

3.6 WHEN an engaged topic running low on unseen clips qualifies for endless expansion THEN the system SHALL CONTINUE TO queue topic expansion as before, independent of the self-heal retry budget.

3.7 WHEN the path is low on unseen clips and nothing is processing THEN the system SHALL CONTINUE TO auto-extend the path as before.

## Bug Condition and Properties

The following pseudocode formalizes the bug condition and the fix/preservation properties. `F` is the current (unfixed) behavior; `F'` is the fixed behavior.

### Bug Condition

```pascal
FUNCTION isBugCondition(X)
  INPUT: X of type TopicFeedState
         (X.has_clips: boolean,
          X.is_generating: boolean,
          X.attempts: integer,        // prior failed/empty generation attempts
          X.cooldown_elapsed: boolean) // cooldown window since last attempt passed
  OUTPUT: boolean

  // The bug fires for a topic that has no clips, is not already generating,
  // and is being considered for a self-heal trigger. Under the defect this
  // ALWAYS re-triggers (no attempt cap, no cooldown), so the topic can loop
  // forever and never converges to a terminal outcome.
  RETURN (NOT X.has_clips) AND (NOT X.is_generating)
END FUNCTION
```

### Property: Fix Checking (bounded retries + terminal state)

```pascal
// For every buggy input, the fixed system must bound retries and converge.
FOR ALL X WHERE isBugCondition(X) DO
  decision <- F'(X)   // {trigger_generation | skip} + reported processing flag

  // 1. Retries are bounded: never trigger once attempts exhausted, and
  //    never trigger during an active cooldown.
  IF X.attempts >= MAX_ATTEMPTS OR NOT X.cooldown_elapsed THEN
    ASSERT decision.trigger_generation = false
  END IF

  // 2. Terminal convergence: a topic that is out of retry budget and still
  //    has no clips must report a terminal, non-processing state.
  IF X.attempts >= MAX_ATTEMPTS AND NOT X.has_clips THEN
    ASSERT decision.processing = false        // spinner can stop
    ASSERT decision.terminal_failed = true    // UI can show a useful message
  END IF
END FOR
```

### Property: Preservation Checking

```pascal
// For every non-buggy input, the fixed system behaves identically to the original.
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT F(X) = F'(X)
END FOR
```

`MAX_ATTEMPTS` and the cooldown window are tunable policy constants to be finalized in design; they must be chosen so that total YouTube quota spend per topic is strictly bounded (each attempt costs roughly 4 searches / 400 units against a 10,000 unit/day quota).
