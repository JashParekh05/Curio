# Implementation Plan: Feed Clip Generation Hang Bugfix

## Overview

Exploration-first and test-first. Task 1 surfaces counterexamples that prove the
bug on the UNFIXED code (unbounded self-heal re-trigger, no cooldown/cap, and the
silent segmentation zero-out). Task 2 captures the non-buggy behavior that must be
preserved, observed on the UNFIXED code. Only then is the fix applied, in the order
of the design's "Changes Required": a new pure policy module, the `FeedResponse`
field, the `get_path_feed` gate + terminal wiring, the `_process_single_topic`
attempt record/clear, the `_node_segment` fallback, and finally the frontend.

New logic sits behind pure seams (`should_self_heal` / `is_terminal_failed` are
DB-free and clock-free), property invariants use Hypothesis (already in the repo),
and orchestration uses the `FakeDB` from `backend/tests/conftest.py`. The in-process
`_self_heal_attempts` dict is cleared in each test's `setup_method`, mirroring
`TestShouldExpandTopic` clearing `te._expanding`. Backend runs with
`cd backend && .venv/bin/python -m pytest -q`; the frontend is verified with
`npx tsc --noEmit`. ASCII only.

## Tasks

- [x] 1. Write bug condition exploration test (BEFORE implementing the fix)
  - **Property 1: Bug Condition** - Unbounded self-heal re-trigger and segmentation zero-out
  - **CRITICAL**: This test MUST FAIL on unfixed code - failure confirms the bug exists
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: This test encodes the expected bounded/terminal/graceful behavior - it validates the fix when it passes after implementation
  - **GOAL**: Surface counterexamples that demonstrate the runaway loop and the silent zero-out
  - **Scoped PBT Approach**: For the deterministic gate and node behavior, scope the property to concrete failing cases so the bug reproduces reliably
  - Create `backend/tests/test_self_heal.py` with `setup_method` clearing `app.services.self_heal_state._self_heal_attempts` (mirror `TestShouldExpandTopic`)
  - Case A (unbounded re-trigger): drive `get_path_feed` with `FakeDB` for an empty, non-generating topic across simulated repeated polls and assert the DESIRED bound - background generation is triggered at most `MAX_SELF_HEAL_ATTEMPTS` times for that slug. On unfixed code the gate `not clips and not is_generating` fires on every poll, so this assertion FAILS
  - Case B (no cooldown): two consecutive polls for the same empty slug both queue a background task; assert the second is suppressed. On unfixed code both fire - FAILS
  - Case C (no terminal state): assert an out-of-budget empty topic reports `processing=false` and `failed=true`. On unfixed code there is no `failed` field and `processing` is always true for empty - FAILS
  - Case D (segmentation zero-out): call `_node_segment` with one transcript-bearing video where `_identify_segments` is monkeypatched to return `[]`; assert `len(result["clips"]) >= 1`. On unfixed code it contributes 0 clips - FAILS
  - Run on UNFIXED code: `cd backend && .venv/bin/python -m pytest -q tests/test_self_heal.py`
  - **EXPECTED OUTCOME**: Test FAILS (this is correct - it proves the bug exists)
  - Document the counterexamples (e.g. "empty topic re-triggered on every one of N polls", "transcript video with empty segmentation -> 0 clips") to confirm the root cause
  - Mark complete when the test is written, run, and the failure is documented
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6_

- [x] 2. Write preservation tests (BEFORE implementing the fix)
  - **Property 2: Preservation** - Non-buggy feed and segmentation behavior unchanged
  - **IMPORTANT**: Follow observation-first methodology - record what the UNFIXED code does for non-buggy inputs, then assert it
  - Add a preservation test class to `backend/tests/test_self_heal.py` (same `setup_method` clearing `_self_heal_attempts`)
  - Observe + assert on UNFIXED code (these PASS today and must keep passing):
    - Populated topic: `get_path_feed` with `FakeDB` seeded with clips for a slug returns those clips with `processing=false`
    - Generating topic: a slug in `generating_slugs` reports `processing=true` and queues no duplicate background task
    - In-budget empty topic: first poll (no prior attempts) queues exactly one background generation task
    - Non-empty segmentation: `_node_segment` with a transcript that segments to N>=1 segments produces exactly N clips and no extra `base` clip
  - Use Hypothesis to generate non-buggy feed states (populated or generating) and assert the observed `processing` value and clip set are unchanged
  - Run on UNFIXED code: `cd backend && .venv/bin/python -m pytest -q tests/test_self_heal.py -k preservation`
  - **EXPECTED OUTCOME**: Tests PASS (this confirms the baseline behavior to preserve)
  - Mark complete when the tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.5_

- [x] 3. Fix for the feed clip generation hang

  - [x] 3.1 Add `backend/app/services/self_heal_state.py` pure policy + attempt tracker
    - Add `MAX_SELF_HEAL_ATTEMPTS = 3` and `SELF_HEAL_COOLDOWN_SECONDS = 300` with the quota-math comments from the design (each attempt ~400 YouTube units against 10,000/day)
    - Add in-process `_self_heal_attempts: dict[str, tuple[int, float]]` (slug -> count, last monotonic time)
    - Add pure `should_self_heal(has_clips, is_generating, attempts, last_attempt_age_seconds)` and `is_terminal_failed(has_clips, is_generating, attempts)` - no DB, no clock, no globals read for the decision
    - Add `read(slug) -> (attempts, age | None)`, `record_attempt(slug)`, `clear(slug)` using `time.monotonic()`
    - _Bug_Condition: isBugCondition(X) = (NOT has_clips) AND (NOT is_generating)_
    - _Expected_Behavior: should_self_heal / is_terminal_failed pseudocode from design_
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 3.2 Write pure unit + property tests for `self_heal_state`
    - Unit (`should_self_heal`): cap boundary (attempts == cap-1 triggers, == cap does not), cooldown boundary (age just under vs at `SELF_HEAL_COOLDOWN_SECONDS`), `attempts==0` ignores cooldown, `has_clips` / `is_generating` short-circuit to false
    - Unit (`is_terminal_failed`): true only when empty AND not generating AND attempts >= cap
    - Unit (`read` / `record_attempt` / `clear`): count increments, monotonic age is computed, `clear` removes the entry, `read` of an unknown slug returns `(0, None)`
    - Hypothesis (Property 1 invariants): for random `(has_clips, is_generating, attempts, last_attempt_age_seconds)`, assert never trigger when `attempts >= cap`, never trigger when `attempts > 0 and age < cooldown`, and `is_terminal_failed` implies a terminal state
    - Run: `cd backend && .venv/bin/python -m pytest -q tests/test_self_heal.py`
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [x] 3.3 Add `failed: bool = False` to `FeedResponse` in `backend/app/models/schemas.py`
    - Backward-compatible default preserves every existing producer/consumer
    - _Preservation: Property 3 - default false keeps non-terminal cases unchanged_
    - _Requirements: 2.4_

  - [x] 3.4 Replace the self-heal gate and wire the terminal state in `backend/app/api/feed.py`
    - Import `from app.services import self_heal_state`
    - Per slug compute `attempts, last_age = self_heal_state.read(slug)` and `has_clips = bool(clips)`
    - Replace `if not clips and not is_generating:` with `if self_heal_state.should_self_heal(has_clips, is_generating, attempts, last_age): missing_slugs.append(slug)`
    - Compute `failed = self_heal_state.is_terminal_failed(has_clips, is_generating, attempts)` and build `FeedResponse(..., processing=is_generating or (not has_clips and not failed), failed=failed)`
    - Carry `failed` through the cross-topic dedupe rebuild of `FeedResponse`
    - Leave the expansion and path auto-extension paths unchanged
    - _Bug_Condition: isBugCondition(X) considered in the per-slug self-heal gate_
    - _Expected_Behavior: bounded trigger + terminal processing=false/failed=true from design_
    - _Preservation: Property 3, 6 - populated/generating topics and expansion/extension unchanged_
    - _Requirements: 2.1, 2.3, 2.4, 3.1, 3.2, 3.3, 3.6, 3.7_

  - [x] 3.5 Record/clear attempt outcome in `_process_single_topic` (`backend/app/api/topics.py`)
    - Accumulate total clips stored across the run (sum `run_pipeline` return values, or a post-run clip-count check on the slug)
    - In the `finally`, alongside `generating_slugs.discard(slug)`: `self_heal_state.clear(slug)` if total stored >= 1, else `self_heal_state.record_attempt(slug)`
    - This covers both the initial POST path and the self-heal path in one place
    - _Expected_Behavior: attempt budget survives the generating_slugs lifecycle_
    - _Preservation: Property 4 - a successful attempt clears tracking_
    - _Requirements: 2.2, 3.4_

  - [x] 3.6 Add graceful segmentation fallback + observability in `_node_segment` (`backend/app/agents/pipeline_agent.py`)
    - For a video with a transcript, capture `segments` (empty on `_identify_segments` error/`[]`)
    - When `segments` is non-empty, append clips exactly as today (unchanged)
    - When `segments` is empty, append the `base` clip and log a distinct "segmentation empty" reason so a 0-clip outcome is distinguishable from a 0-video search
    - Leave the no-transcript branch and the existing `except` branch unchanged
    - _Bug_Condition: transcript-bearing video with empty segmentation yields 0 clips_
    - _Expected_Behavior: contribute >= 1 (base) clip and log a distinct reason_
    - _Preservation: Property 5 - non-empty segmentation unchanged_
    - _Requirements: 2.5, 2.6, 3.5_

  - [x] 3.7 Add FakeDB orchestration tests for the fixed seams
    - `get_path_feed` (FakeDB, `_self_heal_attempts` cleared in setup): an empty topic across simulated polls reaches the cap, stops queuing background tasks, and reports `processing=false, failed=true`; populated and generating topics are unaffected; expansion/extension triggers fire independent of the self-heal budget
    - `_process_single_topic` (FakeDB): a 0-clip run records an attempt; a >=1-clip run clears tracking (assert via `self_heal_state.read(slug)`)
    - `_node_segment` property test (Hypothesis): for random segment lists, a transcript-bearing video yields `max(len(segments), 1)` clips
    - Run: `cd backend && .venv/bin/python -m pytest -q`
    - _Requirements: 2.1, 2.2, 2.4, 2.5, 3.1, 3.2, 3.4, 3.5, 3.6, 3.7_

  - [x] 3.8 Add the frontend terminal-empty handling
    - `frontend/lib/api.ts`: add optional `failed?: boolean` to the `FeedResponse` type
    - `frontend/app/feed/page.tsx`: in the path branch of `loadFeed`, compute `allTerminal = feeds.length > 0 && feeds.every((f) => f.failed || (!f.processing && f.clips.length === 0))`; when `allClips.length === 0 && allTerminal`, render the existing "No clips found" empty-state screen and stop polling instead of waiting for the 5-minute timeout
    - Keep the existing `setProcessing(feeds.some((f) => f.processing))` spinner behavior
    - Verify: `cd frontend && npx tsc --noEmit`
    - _Requirements: 2.4_

  - [x] 3.9 Verify bug condition exploration test now passes
    - **Property 1: Expected Behavior** - Bounded self-heal, terminal state, and graceful segmentation
    - **IMPORTANT**: Re-run the SAME test from task 1 - do NOT write a new test
    - The test from task 1 encodes the expected bounded/terminal/graceful behavior
    - Run: `cd backend && .venv/bin/python -m pytest -q tests/test_self_heal.py`
    - **EXPECTED OUTCOME**: Test PASSES (confirms the bug is fixed - retries bounded, terminal state surfaced, segmentation yields >= 1 clip)
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

  - [x] 3.10 Verify preservation tests still pass
    - **Property 2: Preservation** - Non-buggy feed and segmentation behavior unchanged
    - **IMPORTANT**: Re-run the SAME tests from task 2 - do NOT write new tests
    - Run: `cd backend && .venv/bin/python -m pytest -q tests/test_self_heal.py -k preservation`
    - **EXPECTED OUTCOME**: Tests PASS (confirms no regressions for populated/generating/in-budget/non-empty-segmentation cases)
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

- [x] 4. Checkpoint - ensure all tests pass and spot-check the new policy
  - Run the full backend suite: `cd backend && .venv/bin/python -m pytest -q`
  - Run the frontend type check: `cd frontend && npx tsc --noEmit`
  - Mutation spot-check the new pure policy: temporarily break the cap check in `should_self_heal` (e.g. `>=` -> `>`) and the cooldown comparison (e.g. `<` -> `<=`), confirm a `test_self_heal.py` test fails for each, then revert
  - Ensure all tests pass; ask the user if questions arise
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

## Task Dependency Graph

```json
{
  "waves": [
    { "wave": 1, "tasks": ["1"] },
    { "wave": 2, "tasks": ["2"] },
    { "wave": 3, "tasks": ["3.1", "3.3"] },
    { "wave": 4, "tasks": ["3.2", "3.4", "3.5", "3.6"] },
    { "wave": 5, "tasks": ["3.7", "3.8"] },
    { "wave": 6, "tasks": ["3.9", "3.10"] },
    { "wave": 7, "tasks": ["4"] }
  ]
}
```

## Notes

- Exploration (task 1) and preservation (task 2) tests are written and run on the
  UNFIXED code first: task 1 must FAIL (proves the bug), task 2 must PASS (captures
  the baseline to preserve).
- Implementation follows the design's "Changes Required" order: pure policy module
  (3.1) and the schema field (3.3) first, then the orchestration seams (3.4-3.6),
  then the frontend (3.8).
- All backend changes are best-effort: never block clip delivery, log and skip on
  failure, and keep new decisions behind pure, clock-free seams.
- The frontend has no automated test runner; verification is `npx tsc --noEmit`
  plus the terminal-empty state assertion in task 3.7/3.8.
