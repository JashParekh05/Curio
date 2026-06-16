-- Learning-arc coherence migration
-- Run this in Supabase SQL editor (Dashboard → SQL Editor)
--
-- Additive and nullable only — safe to run on an existing clips table without
-- backfill. New topics populate topic_arcs / topic_arc_roles and gain the
-- realized-arc clip columns; clips generated before this feature simply have
-- NULL here and fall back to section_index / hook-based ordering at delivery.

-- 1. Per-topic planned arc metadata (replaces the implicit fixed 4-section arc).
create table if not exists topic_arcs (
  topic_slug      text primary key,
  concept_type    text not null,
  default_applied boolean not null default false,
  template_empty  boolean not null default false,
  coherence_score real,
  aligned         boolean,
  created_at      timestamptz default now()
);

-- 2. Ordered pedagogical roles of the planned arc (consecutive ordinal from 1).
create table if not exists topic_arc_roles (
  id           uuid primary key default gen_random_uuid(),
  topic_slug   text not null,
  role         text not null,
  ordinal      int  not null,
  unique (topic_slug, ordinal)
);

-- 3. Pool of labeled atoms cut from transcripts.
create table if not exists learning_atoms (
  id              uuid primary key default gen_random_uuid(),
  topic_slug      text not null,
  video_id        text not null,
  source_url      text,
  role            text not null,
  concept         text not null,
  prior_knowledge jsonb not null default '[]'::jsonb,
  start_seconds   real not null,
  end_seconds     real not null,
  transcript      text,
  created_at      timestamptz default now()
);
create index if not exists learning_atoms_topic_idx on learning_atoms (topic_slug);

-- 4. Detected defects for observability / debugging the repair loop.
create table if not exists coherence_defects (
  id             uuid primary key default gen_random_uuid(),
  topic_slug     text not null,
  defect_type    text not null,
  clip_positions jsonb not null default '[]'::jsonb,
  role           text,
  resolved       boolean not null default false,
  created_at     timestamptz default now()
);
create index if not exists coherence_defects_topic_idx on coherence_defects (topic_slug);

-- 5. Clip columns for realized arc + tiebreaker. Nullable; pre-feature rows fall
--    back to section_index / hook ordering at delivery.
alter table clips add column if not exists pedagogical_role text;
alter table clips add column if not exists role_ordinal int;
alter table clips add column if not exists concept_label text;
alter table clips add column if not exists engagement_score real;
alter table clips add column if not exists coherence_score real;
create index if not exists clips_role_ordinal_idx on clips (topic_slug, role_ordinal);
