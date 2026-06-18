-- Engagement telemetry migration
-- Run this in Supabase SQL editor (Dashboard → SQL Editor), manually, by the operator.
--
-- Additive and nullable only — safe to run on existing data with no backfill. Creates
-- the Impression_Store. Every statement is idempotent (create table / index if not
-- exists). Does NOT drop, rename, or alter any column of clip_events, and leaves all
-- existing clip_events rows unchanged. clip_events remains the Watch_Event source;
-- this feature read-joins it. Every added column is nullable or defaulted, so there is
-- nothing to backfill.

-- Impression_Store: one row per clip served to a learner on a Feed_Surface.
create table if not exists impressions (
  id                uuid primary key default gen_random_uuid(),
  clip_id           text not null,
  session_id        text,                                -- null for discover (no session)
  user_id           text,                                -- null when learner unresolved (Req 1.9)
  feed_surface      text not null,                       -- 'discover' | 'learn_path'
  feed_position     int  not null,                       -- 0-based ordinal within the serve (Req 1.4)
  -- Served_Context snapshot at serve time; each nullable (Req 1.3, 1.5)
  pedagogical_role  text,
  content_level     text,
  source_platform   text,
  topic_slug        text,
  served_at         timestamptz not null default now(),  -- UTC serve time (Req 1.6)
  created_at        timestamptz default now()
);

-- Read indexes: per-session journey, per-user journey, per-clip watch-event join.
create index if not exists impressions_session_idx on impressions (session_id, served_at);
create index if not exists impressions_user_idx    on impressions (user_id, served_at);
create index if not exists impressions_clip_idx    on impressions (clip_id);
