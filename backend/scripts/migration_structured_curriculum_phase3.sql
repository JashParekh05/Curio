-- Structured Learn Curriculum migration (Phase 3, Req 5) -- ADDITIVE STEP ONLY.
-- Run this in the Supabase SQL editor (Dashboard -> SQL Editor).
--
-- Run MANUALLY by the operator, never at application startup (Req 5.2) and only
-- after a Pre_Migration_Snapshot is recorded. This file is the ADDITIVE step of
-- the structured-learn-curriculum Phase 3 Staged_Migration (Req 5.1): it only
-- adds a new table (learning_progress) plus a supporting index. It leaves every
-- existing table unchanged and adds no destructive change -- the matching
-- reverse step (recorded by the staged_migration runner) is applied separately
-- and is shown below for the operator/runner.
--
-- Every statement is idempotent (create table if not exists / create index if
-- not exists), so re-running is safe. The table is brand new, so there are no
-- pre-feature rows to read back and no backfill is required (Req 5.3): a subject
-- with no row is simply 'not_started'.
--
-- The mastery_score column carries a `check (mastery_score between 0.0 and 1.0)`
-- constraint. All mastery_score values are produced by the Mastery_Compute core
-- (app/services/mastery.py topic_mastery), which already clamps the score to
-- [0,1] and rounds to 4 dp before the progress.py shell upserts it, so the check
-- constraint can never be violated by the shell (Req 5.4).
--
-- Column names and types align with the shell that reads/writes them:
--   learning_progress  <- services/progress.py (upsert per (subject, topic_slug))
--
-- RECORDED REVERSE STEP (operator/runner -- drop exactly what is added here):
--   drop table if exists learning_progress;

-- 1. Per-(subject, topic) progression row. `subject` is the user id or session
--    id; mastery_score is the clamped [0,1] Mastery_Compute output; status is
--    one of not_started|in_progress|mastered. The check constraint mirrors the
--    core's clamp (Req 5.4) and the subject index supports per-subject,
--    per-level reads for the progress bar and mastery badges.
create table if not exists learning_progress (
  subject        text not null,          -- user_id or session_id
  topic_slug     text not null,
  level          text,                   -- Content_Level band (nullable -> unlevelled)
  status         text not null default 'not_started',  -- not_started|in_progress|mastered
  mastery_score  real not null default 0.0 check (mastery_score >= 0.0 and mastery_score <= 1.0),
  updated_at     timestamptz default now(),
  primary key (subject, topic_slug)
);
create index if not exists learning_progress_subject_idx
  on learning_progress (subject, level);
