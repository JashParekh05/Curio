-- Structured Learn Curriculum migration (Phase 1, Req 5) -- ADDITIVE STEP ONLY.
-- Run this in the Supabase SQL editor (Dashboard -> SQL Editor).
--
-- Run MANUALLY by the operator, never at application startup (Req 5.2) and only
-- after a Pre_Migration_Snapshot is recorded. This file is the ADDITIVE step of
-- the structured-learn-curriculum Phase 1 Staged_Migration (Req 5.1): it only
-- adds new nullable columns (and a supporting index). It leaves every existing
-- row in clips/learning_paths unchanged and adds no destructive change -- the
-- matching reverse step (recorded by the staged_migration runner) is applied
-- separately and is shown below for the operator/runner.
--
-- Every statement is idempotent (add column if not exists / create index if not
-- exists), so re-running is safe. Both columns are nullable, so pre-feature rows
-- read back as NULL and fall back to existing behavior with no backfill (Req 5.3):
--   - NULL clips.level          -> derive from the owning topic's difficulty at
--                                  read time (level_filter.clip_level).
--   - NULL learning_paths.levels -> the frontend renders a single implicit level
--                                  (current behavior).
--
-- Column names and types align with the shells that read/write them:
--   clips.level             <- clip_slot / segment_mapper / pipeline_agent
--   learning_paths.levels   <- the Level_Grouping shell (serialized LeveledPath)
--
-- RECORDED REVERSE STEP (operator/runner -- drop exactly what is added here):
--   drop index if exists clips_level_idx;
--   alter table clips drop column if exists level;
--   alter table learning_paths drop column if exists levels;

-- 1. Clip level slot. The Content_Level band a clip was retrieved for. Nullable
--    and denormalized so Discover/feed can rank by level without joining topics
--    on every read. NULL -> derive from the owning topic's difficulty at read
--    time (level_filter.clip_level), so pre-feature clips need no backfill.
alter table clips add column if not exists level text;
create index if not exists clips_level_idx on clips (level);

-- 2. Leveled path projection. The serialized LeveledPath (ordinal -> name ->
--    topic_slugs) produced purely by Level_Grouping.group_levels. Nullable; NULL
--    on legacy paths renders a single implicit level. The authoritative topic
--    order remains learning_paths.topic_slugs.
alter table learning_paths add column if not exists levels jsonb;
