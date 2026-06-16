# Feed Clip Generation Hang Bugfix Design

## Overview

The path feed can spin forever because an empty-finishing topic is re-triggered for background generation on every ~4s poll. The `generating_slugs` guard in `backend/app/api/topics.py` only de-dupes concurrent runs and is cleared in a `finally` block, so a topic that finishes with 0 clips becomes immediately eligible again on the next poll. With no cooldown and no attempt cap, the loop runs ~400 YouTube Data API units per attempt and can drain the 10,000 unit/day quota in under a minute. Two related defects compound the hang: a topic that can never produce clips still reports `processing: true` forever (the UI never stops spinning short of its 5-minute timeout), and a transient segmentation miss silently yields zero clips for an otherwise-viable video.

The fix has three surgical, best-effort parts that match the existing codebase style:

1. **Bounded self-heal retry budget (primary).** Introduce a small, pure, testable policy plus an in-process attempt tracker (mirroring the `_quiz_generating` guard in `backend/app/api/quiz.py` and the `_expanding` throttle in `backend/app/services/topic_expansion.py`). The tracker survives the `generating_slugs` lifecycle so an empty-finishing topic is not re-triggered until a cooldown elapses, and never re-triggered once a max-attempts cap is hit. A successful generation clears the tracking.

2. **Terminal failed/empty state (secondary).** Add a backward-compatible `failed` flag to `FeedResponse` that is set true only when a topic is out of retry budget and still has no clips. The frontend stops polling and shows the existing "No clips found" screen when every topic is terminal-empty and there are no clips.

3. **Graceful segmentation fallback + observability (contributing).** In `_node_segment`, when `_identify_segments` returns `[]` for a video that HAS a transcript, fall back to contributing the `base` clip (mirroring the existing except-branch), and emit a distinct log reason so a 0-clip outcome from empty segmentation is observable and distinguishable from a 0-video search.

All changes are best-effort: they never block clip delivery, log and skip on failure, keep new logic behind pure seams, and stay ASCII-only.

## Glossary

- **Bug_Condition (C)**: A path-feed topic that has no clips and is not currently generating (`(NOT has_clips) AND (NOT is_generating)`) being considered for a self-heal trigger. Under the defect this always re-triggers; there is no cap and no cooldown.
- **Property (P)**: The desired behavior for a buggy input - the self-heal decision must be bounded (never trigger past the attempt cap or during a cooldown) and the topic must eventually converge to a terminal non-processing state.
- **Preservation**: Existing behavior that must stay unchanged - populated topics, actively-generating topics, in-budget self-heal, successful-attempt delivery, non-empty segmentation, topic expansion, and path auto-extension.
- **`get_path_feed`**: The endpoint in `backend/app/api/feed.py` that builds per-topic `FeedResponse`s and currently performs unconditional self-heal for every empty, non-generating topic.
- **`generating_slugs`**: The in-process `set[str]` in `backend/app/api/topics.py` marking slugs whose pipeline is currently running. Cleared in a `finally`, so it only de-dupes concurrent runs and does NOT remember failed attempts.
- **`should_self_heal`**: New pure decision function that decides whether a topic should be re-triggered, given `has_clips`, `is_generating`, `attempts`, and `last_attempt_age_seconds`. No DB, no side effects - unit-testable in isolation.
- **Attempt tracker**: New in-process module-level dict mapping `slug -> (attempt_count, last_attempt_monotonic_time)` that survives the `generating_slugs` lifecycle, holding the retry-budget state.
- **`_node_segment`**: The pipeline node in `backend/app/agents/pipeline_agent.py` that turns each transcribed video into one or more clips.
- **MAX_SELF_HEAL_ATTEMPTS / SELF_HEAL_COOLDOWN_SECONDS**: Policy constants bounding retries per slug (see Data Models for chosen values + quota justification).

## Bug Details

### Bug Condition

The bug manifests when `get_path_feed` evaluates an empty, non-generating topic for self-heal. Today the trigger is unconditional: any topic with no clips and not in `generating_slugs` is appended to `missing_slugs` and re-queued, with no memory of how many times it has already failed or how recently. So a topic that completes a generation attempt with 0 clips (search returned nothing / quota-blocked / segmentation empty) is dropped from `generating_slugs` in the `finally` and becomes eligible again on the very next ~4s poll, looping indefinitely.

**Formal Specification:**
```
FUNCTION isBugCondition(X)
  INPUT: X of type TopicFeedState
         (X.has_clips: boolean,
          X.is_generating: boolean,
          X.attempts: integer,         // prior failed/empty generation attempts
          X.cooldown_elapsed: boolean) // cooldown window since last attempt passed
  OUTPUT: boolean

  // The bug fires for a topic that has no clips, is not already generating,
  // and is being considered for a self-heal trigger. Under the defect this
  // ALWAYS re-triggers (no attempt cap, no cooldown), so the topic can loop
  // forever and never converges to a terminal outcome.
  RETURN (NOT X.has_clips) AND (NOT X.is_generating)
END FUNCTION
```

The desired correct behavior for a buggy input is captured by the new pure decision:
```
FUNCTION should_self_heal(has_clips, is_generating, attempts, last_attempt_age_seconds)
  INPUT: feed-state signals for one slug
  OUTPUT: boolean (true = trigger background generation)

  IF has_clips OR is_generating THEN
    RETURN false                       // not a self-heal case
  END IF
  IF attempts >= MAX_SELF_HEAL_ATTEMPTS THEN
    RETURN false                       // attempt cap reached -> terminal
  END IF
  IF attempts > 0 AND last_attempt_age_seconds < SELF_HEAL_COOLDOWN_SECONDS THEN
    RETURN false                       // still in cooldown after a prior attempt
  END IF
  RETURN true                          // in budget -> self-heal
END FUNCTION
```

A topic is terminal-failed (drives the spinner-stop) when it is out of budget and still empty:
```
FUNCTION is_terminal_failed(has_clips, is_generating, attempts)
  RETURN (NOT has_clips) AND (NOT is_generating)
         AND attempts >= MAX_SELF_HEAL_ATTEMPTS
END FUNCTION
```

### Examples

- **Runaway loop (the bug):** A topic finishes generation with 0 clips. Next poll (~4s later) it is empty and not generating, so the current code re-triggers it. This repeats every poll forever. Expected: after the first failed attempt is recorded, the cooldown blocks re-triggering until `SELF_HEAL_COOLDOWN_SECONDS` elapses, and after `MAX_SELF_HEAL_ATTEMPTS` the topic is never re-triggered.
- **Quota exhaustion:** Within ~1 minute the unconditional loop fires ~15 attempts (~6000 units) and climbing; once the daily quota is gone every search returns empty/403 and every topic loops. Expected: per-slug spend is bounded to `MAX_SELF_HEAL_ATTEMPTS` attempts.
- **Forever spinner:** A topic that can never produce clips reports `processing: true` indefinitely. Expected: once terminal-failed, `processing=false` and `failed=true` so the UI can stop.
- **Transient segmentation miss:** `_identify_segments` returns `[]` for a video that has a transcript (LLM error / unparseable JSON). Current code contributes 0 clips for that video. Expected: contribute the `base` clip so the video still yields >= 1 clip.
- **Edge - legitimate re-heal after restart:** Server restarts (in-process attempt state resets to empty). A genuinely-empty topic should self-heal again - acceptable and desired, because a restart is exactly when a fresh re-heal SHOULD be allowed.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- A path-feed topic that already has clips returns those clips scored, ranked, and interleaved exactly as before.
- A topic actively generating (slug in `generating_slugs`) continues to report `processing: true` and is never double-triggered.
- A topic with no clips that is still within its retry budget continues to self-heal (transient loss after restart/OOM still recovers automatically).
- A generation attempt that stores >= 1 clip continues to store and deliver them, and clears any failed-attempt tracking for that slug.
- A video whose transcript segments into >= 1 clip continues to produce exactly those clips, with no extra fallback clip.
- Endless topic expansion for engaged topics continues to queue independently of the self-heal retry budget.
- Path auto-extension when low on unseen clips and nothing processing continues unchanged.

**Scope:**
All inputs that do NOT satisfy the bug condition must be completely unaffected by this fix. This includes:
- Populated topics (have_clips true).
- Topics mid-generation (is_generating true).
- Empty topics still within budget (these still self-heal, identically to today on the first attempt).
- Segmentation that already returns >= 1 segment.
- Topic-expansion and path-extension code paths.

**Note:** The expected correct behavior for buggy inputs is defined in the Correctness Properties section (Property 1 and Property 2). This section focuses on what must NOT change.

## Hypothesized Root Cause

Based on the bug description and code inspection, the causes are well-understood and confirmed by reading the source:

1. **No durable attempt state (primary).** `generating_slugs` is cleared in a `finally` in `_process_single_topic` (`topics.py`), so it only prevents concurrent duplicate runs. Nothing remembers that a topic just finished with 0 clips, so `get_path_feed`'s `if not clips and not is_generating:` check re-queues it on every poll. There is no cap and no cooldown.

2. **`processing` conflates "working" with "empty".** `FeedResponse(processing=is_generating or len(clips) == 0)` reports an unfixable empty topic as processing forever. There is no terminal/failed signal for the frontend to act on, so it spins until its 5-minute client timeout.

3. **Segmentation zero-out has no fallback.** In `_node_segment`, the `if v.get("transcript"):` branch only appends clips from `_identify_segments(...)`. When that returns `[]` (logged LLM error / unparseable JSON, both return `[]`), the loop adds nothing for that video, unlike the `except` branch which appends `base`. A transient miss silently produces 0 clips.

4. **0-clip reason is not observable.** Nothing distinguishes "search returned no videos / quota-or-403" (already surfaced via `_node_search`'s `errors` list + quota-cost logs) from "segmentation returned empty", so operators cannot tell why a topic produced 0 clips.

## Correctness Properties

### Property 1: Bug Condition - Bounded self-heal and terminal convergence

_For any_ input where the bug condition holds (`isBugCondition` returns true, i.e. a topic has no clips and is not generating), the fixed `should_self_heal` decision SHALL be bounded: it SHALL return false whenever `attempts >= MAX_SELF_HEAL_ATTEMPTS`, and SHALL return false whenever a prior attempt exists and `last_attempt_age_seconds < SELF_HEAL_COOLDOWN_SECONDS`. Furthermore, when `attempts >= MAX_SELF_HEAL_ATTEMPTS` and the topic still has no clips, the fixed `get_path_feed` SHALL report a terminal state for that topic (`processing=false` and `failed=true`) so the spinner can stop. Within budget (under the cap and past any cooldown) the decision SHALL still return true so transient losses recover.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4**

### Property 2: Bug Condition - Graceful segmentation fallback and observable reason

_For any_ video that has a transcript but whose segmentation returns an empty list, the fixed `_node_segment` SHALL contribute at least one clip (the `base` clip) for that video rather than zero, and the system SHALL log a reason distinguishing an empty/quota-blocked search from an empty segmentation result so the cause of a 0-clip outcome is observable.

**Validates: Requirements 2.5, 2.6**

### Property 3: Preservation - Non-buggy inputs unchanged

_For any_ input where the bug condition does NOT hold (`isBugCondition` returns false) - a populated topic, an actively-generating topic, or an empty topic still within its retry budget on its first eligible poll - the fixed code SHALL produce the same observable result as the original code: the same clips, the same `processing` value (with `failed` defaulting to false), the same single self-heal trigger when in budget, and no duplicate concurrent trigger.

**Validates: Requirements 3.1, 3.2, 3.3**

### Property 4: Preservation - Successful attempts clear tracking

_For any_ generation attempt that stores one or more clips, the fixed code SHALL continue to store and deliver those clips and SHALL clear the failed/empty attempt tracking for that slug, so the topic thereafter behaves as a normal populated topic with a reset retry budget.

**Validates: Requirements 3.4**

### Property 5: Preservation - Non-empty segmentation unchanged

_For any_ video whose transcript is segmented into one or more clips, the fixed `_node_segment` SHALL produce exactly those clips unchanged, with no extra fallback clip added.

**Validates: Requirements 3.5**

### Property 6: Preservation - Expansion and extension unchanged

_For any_ engaged topic eligible for endless expansion, and _for any_ path low on unseen clips with nothing processing, the fixed code SHALL continue to queue topic expansion and path extension exactly as before, independent of the self-heal retry budget.

**Validates: Requirements 3.6, 3.7**

## Architecture

The fix touches four layers, each with a minimal, isolated change. The new policy lives in a pure function and a tiny in-process state module so it is unit-testable with no DB and so orchestration can be tested with FakeDB.

```
                 get_path_feed (feed.py)                    [orchestration]
                        |
       per slug: has_clips, is_generating  ---------+
                        |                            |
                        v                            v
        should_self_heal(...)  <-- attempts,   self_heal_state (NEW module)
        is_terminal_failed(...)    last_age       attempt tracker:
              [PURE policy, NEW]                   slug -> (count, monotonic_t)
                        |                            ^   record / clear / read
          trigger? ----+----> FeedResponse(             |
                                processing, failed)      | on >=1 clip stored: clear
                                                         | on 0-clip finish: record
                       _process_single_topic (topics.py)-+   [records outcome]
                                |
                                v
                  run_pipeline -> _node_segment (pipeline_agent.py)
                     fallback to base clip on empty segmentation
                     + distinct observability log

       FeedResponse.failed (schemas.py)  --> frontend/app/feed/page.tsx
                                              stop polling + show "No clips found"
                                              when all terminal-empty and no clips
```

### Where the attempt state lives (decision + tradeoff)

The attempt tracker is an **in-process module-level dict** `_self_heal_attempts: dict[str, tuple[int, float]]` mapping `slug -> (attempt_count, last_attempt_monotonic_time)`, living in a new small module `backend/app/services/self_heal_state.py`. This mirrors the existing best-effort in-process guards: `_quiz_generating` (`quiz.py`) and `_expanding` (`topic_expansion.py`). Generation state in this codebase is already in-process and best-effort, so this is consistent.

Monotonic time (`time.monotonic()`) is used for the cooldown so it is immune to wall-clock adjustments; only age-differences matter, never absolute timestamps.

**Tradeoff vs. persisting to the DB:** A DB-backed tracker would survive restarts, but that is the wrong behavior here. A server restart is exactly the moment a legitimate re-heal SHOULD be allowed (the in-flight background task may have been lost on restart/OOM - that is the original reason self-heal exists, per requirement 3.3). Resetting the budget on restart is therefore a feature, not a bug. An in-process dict is also zero-latency, needs no migration, and matches the surrounding code. The only cost is that a multi-process / multi-replica deployment tracks budget per process; given the 4s poll typically hits one warm process and the cap is small, the worst case is `MAX_SELF_HEAL_ATTEMPTS x replicas` attempts per slug - still strictly bounded and far below the runaway loop. This is acceptable for a best-effort self-heal.

### Best-effort and seams

- `should_self_heal` and `is_terminal_failed` are pure - no DB, no clock, no globals - so the policy is unit-testable directly (the clock value is passed in as `last_attempt_age_seconds`).
- `self_heal_state` exposes thin `record_attempt(slug)`, `clear(slug)`, and `read(slug) -> (attempts, last_age_seconds | None)` helpers; reads compute age from `time.monotonic()` internally so callers stay clock-free.
- `get_path_feed` orchestration is exercised with FakeDB (`backend/tests/conftest.py`) plus a cleared `_self_heal_attempts` between tests, matching `TestShouldExpandTopic`'s `setup_method` that clears `te._expanding`.

## Components and Interfaces

### 1. `backend/app/services/self_heal_state.py` (NEW)

Holds the policy constants, the pure decision functions, and the in-process attempt tracker.

```python
import time

# Each self-heal attempt runs the section pipeline (~4 YouTube searches x 100
# units = ~400 units) against a 10,000 units/day free quota. Capping attempts
# per slug bounds worst-case spend to ~1,200 units (3 x 400) before a topic goes
# terminal, leaving headroom for many distinct topics in a day.
MAX_SELF_HEAL_ATTEMPTS = 3
# Cooldown between attempts for the same slug. The feed polls every ~4s; a 5-min
# window means a failing topic is retried at most once per 5 min instead of
# every poll (~75x fewer triggers), and reaches the cap after ~10 min.
SELF_HEAL_COOLDOWN_SECONDS = 300

# slug -> (attempt_count, last_attempt_monotonic_time). Best-effort, in-process;
# reset on restart by design (a restart is when a legitimate re-heal is allowed).
_self_heal_attempts: dict[str, tuple[int, float]] = {}


def should_self_heal(has_clips: bool, is_generating: bool,
                     attempts: int, last_attempt_age_seconds: float | None) -> bool:
    """Pure policy: trigger background generation for an empty topic only when
    within the retry budget (under the attempt cap AND past the cooldown).
    last_attempt_age_seconds is None when there is no prior attempt."""
    if has_clips or is_generating:
        return False
    if attempts >= MAX_SELF_HEAL_ATTEMPTS:
        return False
    if attempts > 0 and last_attempt_age_seconds is not None \
            and last_attempt_age_seconds < SELF_HEAL_COOLDOWN_SECONDS:
        return False
    return True


def is_terminal_failed(has_clips: bool, is_generating: bool, attempts: int) -> bool:
    """Pure: a topic is terminal-failed when it is out of budget and still empty."""
    return (not has_clips) and (not is_generating) and attempts >= MAX_SELF_HEAL_ATTEMPTS


def read(slug: str) -> tuple[int, float | None]:
    """(attempts, age_seconds_since_last_attempt | None) for a slug."""
    entry = _self_heal_attempts.get(slug)
    if entry is None:
        return 0, None
    count, last = entry
    return count, time.monotonic() - last


def record_attempt(slug: str) -> None:
    """Record that a failed/empty generation attempt occurred for slug."""
    count, _ = _self_heal_attempts.get(slug, (0, 0.0))
    _self_heal_attempts[slug] = (count + 1, time.monotonic())


def clear(slug: str) -> None:
    """Clear tracking after a successful generation (>=1 clip stored)."""
    _self_heal_attempts.pop(slug, None)
```

**Interface contract:**
- `should_self_heal` / `is_terminal_failed`: pure, side-effect-free, no DB/clock access.
- `read`: returns current budget signals; `age` is `None` when no attempt recorded.
- `record_attempt`: increments count and stamps `monotonic` time; called when an attempt finishes with 0 clips.
- `clear`: removes the slug entirely; called when an attempt stores >= 1 clip.

### 2. `backend/app/api/feed.py` - `get_path_feed` (MODIFIED)

Replace the unconditional self-heal gate with the pure policy and surface the terminal state.

- Per slug, compute `attempts, last_age = self_heal_state.read(slug)`.
- Gate the existing `missing_slugs.append(slug)` behind `should_self_heal(bool(clips), is_generating, attempts, last_age)` instead of the current `if not clips and not is_generating`.
- When building `FeedResponse`, set `failed=is_terminal_failed(bool(clips), is_generating, attempts)`. `processing` stays `is_generating or len(clips) == 0` for non-terminal cases, but a terminal-failed topic must report `processing=false`; computed as `processing = is_generating or (len(clips) == 0 and not failed)`.
- Keep the existing dedupe/sort/interleave; carry `failed` through the dedupe rebuild of `FeedResponse`.
- The `still_processing` check for path auto-extension is unchanged (a terminal-failed topic now reports `processing=false`, which is correct - it should no longer block extension).

```python
from app.services import self_heal_state

# inside the per-slug loop, replacing the old gate:
is_generating = slug in generating_slugs
attempts, last_age = self_heal_state.read(slug)
has_clips = bool(clips)
if self_heal_state.should_self_heal(has_clips, is_generating, attempts, last_age):
    missing_slugs.append(slug)
failed = self_heal_state.is_terminal_failed(has_clips, is_generating, attempts)
feeds.append(FeedResponse(
    topic_slug=slug,
    clips=clips,
    processing=is_generating or (not has_clips and not failed),
    failed=failed,
))
```

### 3. `backend/app/api/topics.py` - `_process_single_topic` (MODIFIED)

Record the attempt outcome so the budget survives the `generating_slugs` lifecycle. The pipeline already returns `stored_count`; aggregate stored clips across sections and record/clear accordingly in the `finally`/end of the run.

- Track total clips stored across the run (sections sum their `run_pipeline` return, or a post-run clip-count check on the slug).
- On completion: if total stored >= 1, `self_heal_state.clear(slug)`; else `self_heal_state.record_attempt(slug)`.
- This is done before/at the same point as `generating_slugs.discard(slug)` in the `finally`, so the next poll sees the updated budget.

The self-heal trigger in `feed.py` calls `_process_single_topic`, so recording lives in one place and covers both the initial POST path and the self-heal path.

### 4. `backend/app/agents/pipeline_agent.py` - `_node_segment` (MODIFIED)

When a video has a transcript but segmentation returns `[]`, fall back to the `base` clip, mirroring the existing `except` branch. Non-empty segmentation is untouched.

```python
if v.get("transcript"):
    try:
        segments = _identify_segments(v["transcript"], topic_slug, section_context)
    except Exception as exc:
        logger.warning(f"[pipeline_agent] segment failed {vid_id}: {exc}")
        segments = []
    if segments:
        for seg in segments:
            clips.append({ ...unchanged... })
    else:
        # Graceful fallback: a transient empty segmentation (LLM error /
        # unparseable JSON) must still yield the base clip, not zero.
        logger.warning(f"[pipeline_agent] segmentation empty for {vid_id}; using base clip")
        clips.append(base)
else:
    clips.append(base)
```

Observability for the 0-clip case (requirement 2.6): `_node_search` already returns an `errors` list and logs quota cost for "no results / search failed". `_node_segment`'s new warning logs the "segmentation returned empty" reason distinctly. The post-segmentation count log (`{len(clips)} clips after segmentation`) plus these two distinct reasons make the cause observable without new infrastructure.

### 5. `backend/app/models/schemas.py` - `FeedResponse` (MODIFIED)

Add a backward-compatible optional field defaulting to a value that preserves current behavior.

```python
class FeedResponse(BaseModel):
    topic_slug: str
    clips: list[Clip]
    processing: bool = False
    failed: bool = False   # terminal: out of retry budget and still empty
```

`failed` defaults to `false`, so every existing producer and consumer is unaffected (the topic-feed and discover endpoints never set it, the frontend ignores it unless present).

### 6. `frontend/app/feed/page.tsx` (MODIFIED) + `frontend/lib/api.ts` `FeedResponse` type (MODIFIED)

- Add optional `failed?: boolean` to the `FeedResponse` TypeScript type.
- In `loadFeed` (path branch), compute terminal state: `const allTerminal = feeds.length > 0 && feeds.every((f) => f.failed || (!f.processing && f.clips.length === 0));`
- Drive polling off processing as today, but when `allClips.length === 0 && allTerminal`, set a `failed`/terminal flag that short-circuits to the existing "No clips found / try another topic" screen and stops polling, instead of waiting for the 5-minute timeout. Reuse the existing `timedOut` empty-state view (or a sibling `terminalEmpty` boolean rendering the same component) so the UI change is minimal.
- `setProcessing(feeds.some((f) => f.processing))` already turns the spinner off when no topic is processing; the addition only ensures the empty terminal screen shows immediately rather than relying on timeout, and that polling does not restart.

## Data Models

### Attempt tracker entry

`_self_heal_attempts: dict[str, tuple[int, float]]`

| Field | Type | Meaning |
|-------|------|---------|
| key | `str` | topic slug |
| `attempt_count` | `int` | number of failed/empty generation attempts recorded since last clear |
| `last_attempt_monotonic_time` | `float` | `time.monotonic()` at the last recorded attempt; used only for age-difference |

Derived read shape: `read(slug) -> (attempts: int, last_attempt_age_seconds: float | None)`.

### Policy constants and quota justification

| Constant | Value | Justification |
|----------|-------|---------------|
| `MAX_SELF_HEAL_ATTEMPTS` | `3` | Each attempt costs ~400 YouTube units (~4 searches x 100). 3 attempts = ~1,200 units worst case per slug, ~3% of the 10,000/day quota, leaving room for ~8 distinct failing topics/day plus normal traffic. Enough retries to ride out a transient failure, few enough to bound spend hard. |
| `SELF_HEAL_COOLDOWN_SECONDS` | `300` (5 min) | Feed polls every ~4s. Without cooldown a failing topic fires ~15 triggers/min; a 5-min window caps it to ~1 trigger/5min (~75x reduction) and reaches the 3-attempt cap after ~10 min, then goes terminal. |

These are module-level constants (tunable), mirroring `EXPAND_WHEN_UNSEEN_AT_OR_BELOW` / `_EXPAND_COOLDOWN_S` in `topic_expansion.py`.

### `FeedResponse` (extended)

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `topic_slug` | `str` | - | unchanged |
| `clips` | `list[Clip]` | - | unchanged |
| `processing` | `bool` | `False` | unchanged semantics, except a terminal-failed topic now reports `false` |
| `failed` | `bool` | `False` | NEW - true only when out of retry budget and still empty; default preserves all existing behavior |

## Fix Implementation

### Changes Required

**File**: `backend/app/services/self_heal_state.py` (NEW)
1. Add `MAX_SELF_HEAL_ATTEMPTS = 3` and `SELF_HEAL_COOLDOWN_SECONDS = 300` with the quota-math comments.
2. Add the in-process `_self_heal_attempts` dict.
3. Add pure `should_self_heal(...)` and `is_terminal_failed(...)`.
4. Add `read(slug)`, `record_attempt(slug)`, `clear(slug)` (monotonic-clock based).

**File**: `backend/app/models/schemas.py`
1. Add `failed: bool = False` to `FeedResponse` (backward compatible).

**File**: `backend/app/api/feed.py`
1. Import `self_heal_state`.
2. Replace the `if not clips and not is_generating:` gate with `should_self_heal(...)` using `read(slug)`.
3. Compute `failed = is_terminal_failed(...)` and set `processing = is_generating or (not has_clips and not failed)` when building each `FeedResponse`.
4. Carry `failed` through the cross-topic dedupe rebuild.

**File**: `backend/app/api/topics.py`
1. In `_process_single_topic`, accumulate stored clip count across the run.
2. At run end (in the `finally`, alongside `generating_slugs.discard`): `clear(slug)` if >= 1 clip stored, else `record_attempt(slug)`.

**File**: `backend/app/agents/pipeline_agent.py`
1. In `_node_segment`, move the empty-segments case to fall back to `base` and log a distinct "segmentation empty" reason; keep non-empty behavior and the `except` branch unchanged.

**File**: `frontend/lib/api.ts`
1. Add optional `failed?: boolean` to the `FeedResponse` type.

**File**: `frontend/app/feed/page.tsx`
1. Compute an all-terminal-empty condition from `feeds` and render the existing "No clips found" screen + stop polling when there are no clips and every topic is terminal.

## Testing Strategy

### Validation Approach

Two phases: first surface counterexamples that demonstrate the bug on the UNFIXED code, confirming the root cause; then verify the fix bounds retries, surfaces the terminal state, degrades segmentation gracefully, and preserves all non-buggy behavior. Backend runs with `cd backend && .venv/bin/python -m pytest -q`; frontend is verified with `npx tsc --noEmit`. New logic sits behind pure seams; orchestration uses FakeDB from `backend/tests/conftest.py`. In-process state (`_self_heal_attempts`) is cleared in test `setup_method`, mirroring `TestShouldExpandTopic`.

### Exploratory Bug Condition Checking

**Goal**: Surface counterexamples that demonstrate the bug BEFORE implementing the fix, confirming the root-cause analysis. If refuted, re-hypothesize.

**Test Plan**: Drive the pure decision and the `_node_segment` node against the current behavior to show (a) an empty, non-generating topic is always re-triggered with no cap/cooldown, and (b) a transcript that segments to `[]` yields zero clips.

**Test Cases**:
1. **Unbounded re-trigger**: Assert today's gate (`not clips and not is_generating`) returns trigger=true even after many prior attempts and immediately after a prior attempt (will fail/contradict the desired bounded policy on unfixed logic).
2. **No cooldown**: Two consecutive polls 4s apart both trigger generation for the same empty slug (will fail desired policy on unfixed code).
3. **No terminal state**: An empty topic reports `processing=true` regardless of attempt count (no `failed` field exists yet).
4. **Segmentation zero-out**: Feed `_node_segment` a video with a transcript where `_identify_segments` returns `[]`; observe 0 clips contributed for that video (will fail on unfixed code).

**Expected Counterexamples**:
- Self-heal fires on every poll for an empty topic; no attempt cap, no cooldown.
- A transcript-bearing video with empty segmentation contributes zero clips.
- Possible causes: `generating_slugs` cleared in `finally`; `processing` conflates working/empty; `_node_segment` has no empty-segments fallback.

### Fix Checking

**Goal**: Verify that for all inputs where the bug condition holds, the fixed functions produce the expected bounded/terminal/graceful behavior.

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  IF input.attempts >= MAX_SELF_HEAL_ATTEMPTS OR
     (input.attempts > 0 AND NOT input.cooldown_elapsed) THEN
    ASSERT should_self_heal(...) = false
  END IF
  IF input.attempts >= MAX_SELF_HEAL_ATTEMPTS AND NOT input.has_clips THEN
    ASSERT feedResponse.processing = false
    ASSERT feedResponse.failed = true
  END IF
  // segmentation
  ASSERT len(node_segment(video_with_transcript, empty_segmentation).clips) >= 1
END FOR
```

### Preservation Checking

**Goal**: Verify that for all inputs where the bug condition does NOT hold, the fixed functions produce the same result as the original.

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT original(input) = fixed(input)
END FOR
```

**Testing Approach**: Property-based testing (Hypothesis, already used in this repo) is recommended for preservation because it generates many feed-state inputs across the domain, catches edge cases manual tests miss, and gives strong guarantees that non-buggy behavior is unchanged. Pure `should_self_heal` / `is_terminal_failed` are ideal PBT targets (no DB, no clock).

**Test Plan**: Observe behavior on UNFIXED code for populated topics, generating topics, in-budget empties, and non-empty segmentation, then write property/example tests capturing that behavior with `failed` defaulting to false.

**Test Cases**:
1. **Populated topic unchanged**: `should_self_heal(has_clips=true, ...)` is false; `FeedResponse` clips/processing identical, `failed=false`.
2. **Generating topic unchanged**: `is_generating=true` -> no trigger, `processing=true`, `failed=false`, no duplicate trigger.
3. **In-budget empty still self-heals**: first eligible poll (attempts=0) triggers exactly as today.
4. **Successful attempt clears tracking**: after a run stores >= 1 clip, `_self_heal_attempts` has no entry for the slug; budget reset.
5. **Non-empty segmentation unchanged**: a transcript that segments to >= 1 segment produces exactly those clips, no extra base clip.
6. **Expansion / extension unchanged**: expansion and path-extension triggers fire independent of the self-heal budget.

### Unit Tests

- `should_self_heal`: cap boundary (attempts == cap-1 vs cap), cooldown boundary (age just under vs at `SELF_HEAL_COOLDOWN_SECONDS`), `attempts==0` ignores cooldown, has_clips/is_generating short-circuit to false.
- `is_terminal_failed`: true only when empty, not generating, at/over cap.
- `record_attempt` / `clear` / `read`: count increments, monotonic age computed, clear removes entry, read of unknown slug returns `(0, None)`.
- `_node_segment`: transcript + empty segmentation -> exactly one base clip; transcript + N segments -> N clips, no base; no transcript -> base clip (unchanged).

### Property-Based Tests

- Generate random `(has_clips, is_generating, attempts, last_attempt_age_seconds)` and assert the bounded-retry invariants from Property 1 (never trigger past cap or during cooldown; terminal -> processing false + failed true).
- Generate random non-buggy feed states and assert fixed output equals original (Property 3 preservation), with `failed=false` throughout.
- Generate random segment lists and assert `_node_segment` clip count equals max(len(segments), 1) for transcript-bearing videos (Property 2 / Property 5).

### Integration Tests

- `get_path_feed` with FakeDB: empty topic across simulated polls reaches the cap, stops triggering, and reports `processing=false, failed=true`; populated and generating topics are unaffected.
- `_process_single_topic` end-to-end with FakeDB: a 0-clip run records an attempt; a >= 1-clip run clears tracking.
- Frontend: with all topics `failed=true` and no clips, the feed shows "No clips found" and stops polling (verified via `npx tsc --noEmit` for type-safety plus a render/state assertion).
