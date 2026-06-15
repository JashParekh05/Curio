-- Active-learning quiz migration
-- Run this in Supabase SQL editor (Dashboard -> SQL Editor)
--
-- Additive and nullable only -- safe to run on an existing database.

-- 1. Cached multiple-choice questions per topic (generated in the background).
create table if not exists quiz_questions (
  id uuid primary key default gen_random_uuid(),
  topic_slug text not null,
  question text not null,
  options jsonb not null,          -- ["balanced", "full", "sorted on insert"]
  correct_index int not null,
  explanation text not null,
  created_at timestamptz default now()
);
create index if not exists quiz_questions_topic_idx on quiz_questions (topic_slug);

-- 2. One row per answered question (guest or user). Additive history.
create table if not exists quiz_results (
  id uuid primary key default gen_random_uuid(),
  question_id uuid not null,
  topic_slug text not null,
  user_id uuid,
  session_id text,
  chosen_index int not null,
  correct boolean not null,
  points int not null default 0,
  created_at timestamptz default now()
);
create index if not exists quiz_results_user_idx on quiz_results (user_id);
create index if not exists quiz_results_session_idx on quiz_results (session_id);
