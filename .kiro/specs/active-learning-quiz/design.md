# Design Document

Feature: Active-Learning Notes & Quiz

## Overview

The Plan panel evolves into a Study panel: one calm side surface that is both
the notes/outline (topics to sections, already built) and an optional, gamified
multiple-choice quiz per topic, with a running points score. Nothing touches the
main feed; the scroll is never interrupted. Backend mirrors the section/story
passes: questions are generated in the background, quality-gated by an LLM judge,
cached per topic, and graded deterministically.

## Architecture

```
topic generation (existing)
  plan sections -> run pipeline per beat -> run_story_pass
                                              -> generate_and_store_questions (NEW, background)
                                                   gather sections + transcripts
                                                   -> _generate_questions (LLM)
                                                   -> _validate_question (pure gate)
                                                   -> _judge_question (LLM gate, bounded)
                                                   -> store passing questions (quiz_questions)

delivery (study panel)
  GET /api/quiz/{topic}        -> cached questions (incl. answer, for instant grade)
  client grades on tap          -> instant reveal + optimistic points
  POST /api/quiz/{id}/answer    -> 204, fire-and-forget record (server recomputes correctness)
  GET /api/quiz/mastery/{id}    -> per-topic summary + total points
```

Latency-first principle: the answer reveal is instant (client-side grading from
the loaded payload); persistence is off the critical path, exactly like
`recordClipEvent`.

## Components and Interfaces

### Study panel (frontend)

The existing `PlanPanel` gains a points header and a per-topic quiz block under
the section outline. Layout when a topic is expanded:

```
+-------------------------------------------+
|  Your plan          Streak 3   Score 120  |   <- live streak + total points
+-------------------------------------------+
|  Topics mastered: 2 / 8                    |   <- path-level momentum
|  1  Binary Search Trees      [##.]   ^     |   <- progress pips + chevron
|     -- NOTES --                            |
|     1  Why your data is the wrong shape >  |   <- jump to that beat (existing)
|     2  What is a BST                    >  |
|     -- TEST YOURSELF --                    |
|     Q1. A BST guarantees O(log n) search   |
|         only when it is...                 |
|        [ A  balanced            correct ]  |   <- tapped, correct -> green +10
|        [ B  full                        ]  |
|        [ C  sorted on insert            ]  |
|        > Balanced trees keep height ~log n |   <- explanation after answer
|     Q2. ... (unanswered, neutral buttons)  |
+-------------------------------------------+
```

Engagement layer (calm, never pressuring):
- A live **streak** counter (consecutive correct answers in this panel session)
  next to the score; it grows on correct and resets on incorrect.
- A **path progress** line (topics mastered / total) for whole-path momentum.
- A brief "+10" flourish into the score on a first correct answer.
- No timers, no forced answers; streak reset is the only "loss".

Interaction and states:
- Collapsed topic row: number, name, progress pips (filled per correct answer),
  and a "Mastered" pill once the correct-rate threshold is met.
- Expanded: section outline first (notes/navigation), then a "Test yourself"
  block of MCQs.
- On tap: instant client-side grade and reveal (chosen option green/red, correct
  option outlined green, explanation shown); a first correct answer animates a
  "+N" into the header score, and the live **streak** counter ticks up (a wrong
  answer visibly resets it to zero).
- Answered (revisited): renders revealed; tapping again does nothing and never
  re-awards points.
- Not ready yet: a calm "Quiz coming soon" line, never a blocking spinner.
- Questions lazy-load when a topic is first expanded (same pattern as sections).

Frontend wiring: `lib/api.ts` gains `getQuiz`, `recordQuizAnswer` (fire-and-
forget, like `recordClipEvent`), and `getQuizMastery`.

### Quiz service (`app/services/quiz.py`)

Pure, unit-testable seams:
- `_validate_question(raw)` -> normalized question or `None`; enforces 2-4
  options, exactly one in-range `correct_index`, non-empty prompt/explanation.
- `grade(question, chosen_index)` -> `(correct, points)`; deterministic, no LLM.
- `points_for(streak)` -> int; base points plus a streak bonus that grows with
  the streak and is clamped to a max (pure; the client reports the current
  streak, the server clamps and computes so points can't be inflated).
- `summarize_mastery(results)` -> per-topic `{answered, correct, correct_rate,
  points, mastered}` plus total points.
- `_build_question_prompt(topic_name, sections, transcripts)` -> prompt string
  (MCQ-only, substance-over-trivia, curiosity/scenario phrasing so questions feel
  like a fun challenge).

LLM stages (mirror section/story judges):
- `_generate_questions(...)` and `_judge_question(topic_name, q)` with bounded
  regenerate.
- `generate_and_store_questions(topic_slug, topic_name)`: idempotent, best-effort
  orchestration; the only DB-touching function in the module.

### API (`app/api/quiz.py`, prefix `/api/quiz`)

- `GET /{topic_slug}` -> `[{id, question, options, correct_index, explanation}]`;
  empty when none generated. Enables instant client-side grading.
- `POST /{question_id}/answer` `{chosen_index, session_id, streak}` -> `204`.
  Server recomputes correctness via `grade()` (never trusts a client flag),
  awards `points_for(streak)` (clamped) only on a first correct answer per
  (question, user/session), records the result. Best-effort.
- `GET /mastery/{user_id}` (and session variant) -> `summarize_mastery` + total.

### Generation trigger

In `_process_single_topic`, after `run_story_pass`, queue
`generate_and_store_questions(slug, name)` in the background (isolated, best-
effort), matching the existing pass pattern.

## Data Models

Additive, nullable migration `scripts/migration_quiz.sql`:

```sql
create table if not exists quiz_questions (
  id uuid primary key default gen_random_uuid(),
  topic_slug text not null,
  question text not null,
  options jsonb not null,          -- ["balanced","full","sorted on insert"]
  correct_index int not null,
  explanation text not null,
  created_at timestamptz default now()
);
create index if not exists quiz_questions_topic_idx on quiz_questions (topic_slug);

create table if not exists quiz_results (
  id uuid primary key default gen_random_uuid(),
  question_id uuid not null,
  topic_slug text not null,
  user_id uuid,
  session_id text,
  chosen_index int not null,
  correct boolean not null,
  points int not null default 0,
  created_at timestamptz default now()
);
create index if not exists quiz_results_user_idx on quiz_results (user_id);
create index if not exists quiz_results_session_idx on quiz_results (session_id);
```

## Correctness Properties

### Property 1: Validation invariant
`_validate_question` accepts a question IFF it has 2-4 options, exactly one
`correct_index` within range, and non-empty prompt and explanation.
**Validates: Requirements 2.2, 2.3**

### Property 2: Deterministic grading
`grade` awards points IFF `chosen_index == correct_index`; points are 0
otherwise (never negative).
**Validates: Requirements 5.2, 5.3**

### Property 3: Quality gate
A question flagged by the judge as having multiple or no correct answers is never
stored.
**Validates: Requirements 3.1, 3.3**

### Property 4: Additive mastery
`summarize_mastery` is additive (more attempts never reduce recorded history);
"mastered" is true IFF correct_rate >= threshold.
**Validates: Requirements 6.3, 6.4**

### Property 5: Points awarded once
Re-answering an already-answered question never awards points twice.
**Validates: Requirements 5.5**

### Property 6: MCQ-only
Output is always multiple-choice; no code path emits a non-MCQ question.
**Validates: Requirements 2.2**

### Property 7: Streak bonus is bounded and monotonic
`points_for(streak)` is non-decreasing in `streak` and never exceeds the clamp,
so a higher streak never awards fewer points and a client cannot inflate points
by reporting a huge streak.
**Validates: Requirements 5.6**

## Error Handling

- Generation, judging, and storage are best-effort: any LLM/parse/DB failure is
  logged and skipped, never raised, so clip delivery is never blocked.
- `GET /{topic_slug}` returns an empty list (not an error) when no questions
  exist yet; the panel shows "Quiz coming soon".
- `POST .../answer` is fire-and-forget; a recording failure still leaves the
  learner's instant feedback intact.
- Generation is idempotent: a re-run skips topics that already have questions.

## Testing Strategy

- Pure functions get unit + property tests, mutation-spot-checked
  (`_validate_question`, `grade`, `summarize_mastery`, `_build_question_prompt`).
- Orchestration and API endpoints use the existing `FakeDB` (idempotent skip,
  judge-fail discard, never-raise, points-once on repeat answers, server-side
  correctness recompute).
- Frontend is manually verified (no test runner in the repo) plus `tsc --noEmit`.

## Risks / Decisions

- Client-side grading for lowest latency: the GET payload includes
  `correct_index` + explanation so the reveal is instant with no roundtrip;
  recording is fire-and-forget. Tradeoff: answers are visible in the network
  payload, acceptable for a self-directed learning quiz, and the server still
  recomputes correctness so stored results cannot be spoofed.
- Cost is bounded by per-topic caching + background generation.
- Guest-to-account carryover uses the existing same-user-id upgrade, so guest
  results and points persist with no migration.
- Streak is client-side session-only state (resets when the panel/session ends);
  persisted points are the server-recomputed base award. This keeps grading
  spoof-proof and the answer path stateless while still feeling game-like.
