-- Structured Learn Curriculum migration (Phase 2, Req 5) -- ADDITIVE STEP ONLY.
-- Run this in the Supabase SQL editor (Dashboard -> SQL Editor).
--
-- Run MANUALLY by the operator, never at application startup (Req 5.2) and only
-- after a Pre_Migration_Snapshot is recorded. This file is the ADDITIVE step of
-- the structured-learn-curriculum Phase 2 Staged_Migration (Req 5.1): it only
-- adds new columns to quiz_questions to anchor staged checkpoints. It leaves
-- every existing quiz_questions row's content unchanged and adds no destructive
-- change -- the matching reverse step (recorded by the staged_migration runner)
-- is applied separately and is shown below for the operator/runner.
--
-- Every statement is idempotent (add column if not exists), so re-running is
-- safe. The `stage` column is NOT NULL with default 'check', which matches
-- Stage_Anchor.DEFAULT_STAGE: existing rows become topic-wide 'check' questions
-- via the column default, so NO value backfill is required (Req 5.3). The
-- `section_index` column is nullable; pre-feature rows read back as NULL
-- (topic-wide, the existing behavior).
--
-- Column names and types align with the shells that read/write them:
--   quiz_questions.stage          <- Stage_Anchor / quiz.py (pre|check|post)
--   quiz_questions.section_index  <- Stage_Anchor / quiz.py (beat anchor 0..3)
--
-- RECORDED REVERSE STEP (operator/runner -- drop exactly what is added here):
--   alter table quiz_questions drop column if exists stage;
--   alter table quiz_questions drop column if exists section_index;

-- 1. Checkpoint stage. Which staged checkpoint a question belongs to
--    (pre|check|post). NOT NULL with default 'check' so existing questions
--    become topic-wide 'check' questions with no backfill; the default matches
--    Stage_Anchor.DEFAULT_STAGE.
alter table quiz_questions add column if not exists stage text not null default 'check';

-- 2. Section (beat) anchor. The 0..3 beat a 'check' question is anchored to;
--    NULL for 'pre'/'post' (topic-wide) and for pre-feature rows.
alter table quiz_questions add column if not exists section_index int;
