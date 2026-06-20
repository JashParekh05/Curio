-- Alternative content streams migration
-- Run this in Supabase SQL editor (Dashboard → SQL Editor)
--
-- Run manually by the operator. Additive and nullable only — safe to run on
-- existing tables without backfill. Every statement is idempotent (create table
-- if not exists / add column if not exists). Pre-feature clips carry NULL
-- provenance and default to the 'youtube' provider, so no backfill is required.

-- 1. Operator-configured Provider_Registry. One row per Content_Provider. The
--    registry records which providers are enabled, their declared
--    Provider_Capabilities, and their Provider_Cost_Policy. (Req 3.1, 3.7, 3.8)
create table if not exists provider_registry (
  provider_id   text primary key,                  -- unique within the registry (Req 1.7)
  enabled       boolean not null default true,      -- enabled | disabled (Req 3.1, 3.8)
  capabilities  text[]  not null,                   -- declared Provider_Capabilities (Req 3.7)
  cost_policy   jsonb   not null,                   -- Cost_Units + Spend_Budget (Req 3.7)
  created_at    timestamptz default now(),
  updated_at    timestamptz default now()
);

-- 2. Per-provider spend accounting for the current window, accounted independently
--    per provider. YouTube is NOT stored here — it reuses project_quota_usage via
--    the Key_Pool, so no quota counter is duplicated. (Req 7.2, 7.5)
create table if not exists provider_spend (
  provider_id   text not null,
  window_key    text not null,                      -- accounting window identifier
  spent         int  not null default 0 check (spent >= 0),
  budget        int  not null check (budget >= 0),
  updated_at    timestamptz default now(),
  primary key (provider_id, window_key)
);

-- 3. Provider_Provenance on clips. Nullable + default so pre-feature rows are
--    valid and read back as YouTube. (Req 8.1, 8.3)
alter table clips add column if not exists provider_id text default 'youtube';  -- originating Provider_Id
alter table clips add column if not exists external_id text;                    -- provider's external item id
alter table clips add column if not exists content_id  text;                    -- cross-provider dedup key (Req 8.3)
create index if not exists clips_provenance_idx on clips (topic_slug, provider_id, external_id);
create index if not exists clips_content_idx    on clips (topic_slug, content_id);

-- 4. Per-provider, per-query result cache for non-YouTube providers, mirroring the
--    youtube_search_cache reuse-before-spend convention. youtube_search_cache and
--    transcript_cache stay the YouTube provider's caches, unchanged. (Req 7.6)
create table if not exists provider_search_cache (
  provider_id  text not null,
  query        text not null,
  items        jsonb not null,
  created_at   timestamptz default now(),
  primary key (provider_id, query)
);
