-- Story-quality pass migration
-- Run this in Supabase SQL editor (Dashboard → SQL Editor)
--
-- Additive and nullable only — safe to run on an existing clips table without
-- backfill. Clips generated before the story pass simply have NULL here and
-- fall back to engagement-based ordering at delivery time.

-- 1. Narrative-director score for the clip's topic sequence (0..1).
--    Same value across a topic's clips; used as a quality signal / flag.
alter table clips
  add column if not exists story_score real;

-- 2. The clip's position in the story-optimized sequence (within its beat).
--    Lower = earlier. Delivery orders a beat by this when present.
alter table clips
  add column if not exists narrative_rank int;
