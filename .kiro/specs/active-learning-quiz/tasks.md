# Implementation Plan: Active-Learning Notes & Quiz

## Overview

Incremental and test-first. Pure logic is unit/property tested and mutation-spot-
checked; DB/orchestration uses the existing `FakeDB`; the frontend is manually
verified (no runner in repo) plus `tsc --noEmit`. Each task lists the
requirements it satisfies.

## Tasks

- [x] 1. Add `app/services/quiz.py` pure MCQ helpers and constants
  - `POINTS_PER_CORRECT`, `MASTERED_THRESHOLD`
  - `_validate_question(raw)`: 2-4 options, exactly one in-range `correct_index`,
    non-empty prompt/explanation (the hard MCQ gate)
  - `grade(question, chosen_index) -> (correct, points)`: deterministic, no LLM
  - `points_for(streak) -> int`: base + clamped streak bonus (pure)
  - `summarize_mastery(results)`: per-topic answered/correct/rate/points/mastered
    plus total points
  - _Requirements: 2.2, 2.3, 5.2, 5.3, 5.6, 6.2, 6.4, 7.3_

- [x] 2. Write `tests/test_quiz.py` for the pure helpers
  - validator rejects 0 / 2+ correct, out-of-range index, <2 or >4 options, blank
    fields; accepts a clean question
  - grade: correct -> points, incorrect -> 0; property: points 0 unless match
  - points_for: property — non-decreasing in streak and never exceeds the clamp
  - summarize_mastery: rates, mastered threshold boundary, additive attempts,
    empty input
  - mutation-spot-check the validator, grade sign, and streak clamp; confirm
    fail, then revert
  - _Requirements: 2.2, 5.2, 5.3, 5.6, 6.4, 7.3_

- [x] 3. Add the LLM stages to `quiz.py`
  - `_build_question_prompt(...)`: pure; MUST instruct MCQ-only and substance-
    over-trivia
  - `_generate_questions(...)`, `_judge_question(topic_name, q)` with bounded
    regenerate (mirrors section/story judges)
  - _Requirements: 2.1, 2.6, 3.1, 3.2, 3.3_

- [x] 4. Test prompt building and judge gating
  - prompt asserts MCQ-only + anti-trivia instructions present
  - mocked judge: a flagged question is regenerated then discarded after the
    bound; only passing questions survive
  - _Requirements: 2.2, 3.1, 3.2, 3.3, 7.3_

- [x] 5. Add `generate_and_store_questions(topic_slug, topic_name)` orchestration
  - idempotent (skip if cached), best-effort (never raises); gather sections +
    sampled clip transcripts; generate -> validate -> judge -> store passes
  - fake-DB tests: skip when cached, store only validated+judged, never raise on
    LLM/DB failure
  - _Requirements: 2.1, 2.4, 2.5, 3.3, 7.1, 7.2_

- [x] 6. Add `scripts/migration_quiz.sql` (additive, nullable)
  - `quiz_questions` and `quiz_results` tables + indexes
  - _Requirements: 6.1, 6.3, 7.4_

- [x] 7. Trigger generation after a topic's story pass
  - in `_process_single_topic`, queue `generate_and_store_questions` in the
    background after `run_story_pass` (isolated, best-effort)
  - _Requirements: 2.1, 2.4, 7.1_

- [x] 8. Add `app/api/quiz.py` router and register in `main.py`
  - `GET /{topic_slug}` (incl. answer for client grading; empty when none)
  - `POST /{question_id}/answer` -> 204: recompute correctness, award
    `points_for(streak)` (clamped) once per (question, user/session), record;
    best-effort
  - `GET /mastery/{user_id}` (+ session variant)
  - _Requirements: 4.1, 5.1, 5.3, 5.4, 5.5, 5.6, 6.1, 6.2_

- [x] 9. Fake-DB tests for the quiz API
  - GET returns cached / empty; POST records once, never trusts client flag,
    best-effort on failure; mastery summary correct
  - _Requirements: 5.3, 5.4, 5.5, 6.2, 6.3, 7.3_

- [ ] 10. Add quiz client functions to `lib/api.ts`
  - `getQuiz`, `recordQuizAnswer` (fire-and-forget), `getQuizMastery`
  - _Requirements: 4.1, 5.4_

- [ ] 11. Extend `PlanPanel` into the study panel
  - sticky header with total score, live streak, and path progress (mastered /
    total); per-topic pips + "Mastered" pill from mastery
  - lazy `getQuiz` on expand; notes (sections) then "Test yourself" MCQs
  - instant client-side grade + reveal, optimistic score, streak builds on
    correct and resets on incorrect (session-only), `recordQuizAnswer` fire-and-
    forget; answered stays revealed; no double-award; "coming soon" placeholder;
    never auto-open
  - _Requirements: 1.1, 1.2, 1.4, 4.1, 4.2, 4.3, 4.4, 4.5, 5.1, 5.3, 5.6_

- [ ] 12. Final verification
  - full backend suite + coverage + mutation spot-checks on new logic;
    `tsc --noEmit`; manual smoke (answer, see points, reopen -> still revealed)
  - _Requirements: 7.1, 7.2, 7.3_

## Task Dependency Graph

```json
{
  "waves": [
    { "wave": 1, "tasks": ["1"] },
    { "wave": 2, "tasks": ["2", "3", "6"] },
    { "wave": 3, "tasks": ["4", "5"] },
    { "wave": 4, "tasks": ["7", "8"] },
    { "wave": 5, "tasks": ["9", "10"] },
    { "wave": 6, "tasks": ["11"] },
    { "wave": 7, "tasks": ["12"] }
  ]
}
```

## Notes

- Build order favors pure logic first (tasks 1-5), then persistence/API (6-9),
  then frontend (10-11), then verification (12).
- All backend work follows the best-effort, never-block, pure-testable-seam
  conventions used by the section and story passes.
- Frontend has no automated test runner; verification is `tsc --noEmit` plus a
  manual smoke of the study panel.
