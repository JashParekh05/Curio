-- Deep content ingestion migration
-- Run this in Supabase SQL editor (Dashboard → SQL Editor)
--
-- Run manually by the operator. Additive and nullable only — safe to run on
-- existing tables without backfill. Every statement is idempotent (if not
-- exists / create or replace). Pre-feature clips carry NULL in the new columns
-- and fall back to old behavior; no backfill is required.

-- 1. Persisted Ingestion_Queue. One row per Topic; survives process restart so a
--    non-completed job resumes from its recorded stage. (Req 5.3, 5.4)
create table if not exists ingestion_jobs (
  topic_slug      text primary key,                       -- identity for dedupe (Req 5.6)
  stage           text not null default 'fast_preview',   -- 'fast_preview' | 'full_processing'
  status          text not null default 'queued',         -- 'queued'|'running'|'completed'|'failed'
  failure_reason  text,
  created_at      timestamptz default now(),
  updated_at      timestamptz default now()
);
-- Resume reads non-completed jobs.
create index if not exists ingestion_jobs_active_idx
  on ingestion_jobs (status) where status <> 'completed';

-- 2. Per-Source_Video decode->map->judge->admit funnel for observability. (Req 4.6)
create table if not exists ingestion_funnel (
  id           uuid primary key default gen_random_uuid(),
  topic_slug   text not null,
  video_id     text not null,
  idea_units   int  not null default 0 check (idea_units >= 0),
  mapped       int  not null default 0 check (mapped     >= 0),
  passed       int  not null default 0 check (passed     >= 0),
  admitted     int  not null default 0 check (admitted   >= 0),
  created_at   timestamptz default now(),
  unique (topic_slug, video_id)
);
create index if not exists ingestion_funnel_topic_idx on ingestion_funnel (topic_slug);

-- 3. Per-segment verdicts for observability / debugging the judge. (Req 3.x)
create table if not exists segment_verdicts (
  id               uuid primary key default gen_random_uuid(),
  topic_slug       text not null,
  video_id         text not null,
  pedagogical_role text,
  quality_score    real not null check (quality_score >= 0.0 and quality_score <= 1.0),
  fit              text not null,            -- 'belongs' | 'off_role'
  status           text not null,            -- 'passing' | 'failing'
  reason           text,
  created_at       timestamptz default now()
);
create index if not exists segment_verdicts_topic_idx on segment_verdicts (topic_slug);

-- 4. Clip columns for the judge score + Fast_Preview reconciliation. Nullable;
--    pre-feature rows are NULL and remain feed-eligible under old behavior.
alter table clips add column if not exists quality_score real;       -- Per_Segment_Judge (Req 3.2)
alter table clips add column if not exists feed_eligible boolean;    -- reconcile (Req 6.4, 6.5)
alter table clips add column if not exists is_fast_preview boolean;  -- preview vs admitted (Req 6.2, 6.5)
create index if not exists clips_feed_eligible_idx on clips (topic_slug, feed_eligible);
