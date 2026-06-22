-- Cold-start content library migration
-- Run this in Supabase SQL editor (Dashboard → SQL Editor)
--
-- Run manually by the operator. Additive and nullable only — safe to run on
-- existing tables without backfill. Every statement is idempotent (if not
-- exists / create or replace). Pre-feature rows simply carry NULL on the new
-- nullable column and fall back to the old behavior; no backfill is required.

-- 1. Per-project daily YouTube quota usage. One row per (project, window date).
--    A missing row for today means used = 0 (fresh rollover window). Keying on
--    quota_date makes the daily rollover implicit: a new date starts at zero and
--    yesterday's rows are simply not read.
create table if not exists project_quota_usage (
  project_id   text not null,
  quota_date   date not null,              -- Pacific-time window date
  used_units   int  not null default 0 check (used_units >= 0 and used_units <= 10000),
  updated_at   timestamptz default now(),
  primary key (project_id, quota_date)
);
create index if not exists project_quota_usage_date_idx on project_quota_usage (quota_date);

-- Atomic, overspend-safe increment used by charge_and_persist. Caps at 10000 so
-- a row can never exceed the per-project daily quota.
create or replace function increment_quota_usage(p_project text, p_date date, p_cost int)
returns int language plpgsql as $$
declare new_used int;
begin
  insert into project_quota_usage (project_id, quota_date, used_units)
    values (p_project, p_date, least(greatest(p_cost, 0), 10000))
  on conflict (project_id, quota_date) do update
    set used_units = least(project_quota_usage.used_units + greatest(p_cost, 0), 10000),
        updated_at = now()
  returning used_units into new_used;
  return new_used;
end $$;

-- 2. Persisted, prioritized topic backlog (Topic_Frontier). Survives restarts.
create table if not exists topic_backlog (
  topic       text primary key,            -- slug; identity for dedupe + tie-break
  level       text,                        -- Content_Level (nullable -> treated as unlevelled)
  priority    real not null default 0.5 check (priority >= 0.0 and priority <= 1.0),
  status      text not null default 'pending',  -- 'pending' | 'done'
  created_at  timestamptz default now(),
  updated_at  timestamptz default now()
);
-- Selection reads pending items ordered by priority desc, slug asc.
create index if not exists topic_backlog_pending_idx
  on topic_backlog (status, priority desc, topic);

-- 3. Clip / topic Content_Level. clips carry an optional denormalized level so
--    Discover can rank by level without joining topics on every read. NULL ->
--    derive from the owning topic's difficulty at read time (Level_Filter.clip_level),
--    so pre-feature clips need no backfill.
alter table clips add column if not exists content_level text;
create index if not exists clips_content_level_idx on clips (content_level);
-- topics.difficulty already exists and is the source of truth for a topic's level.
