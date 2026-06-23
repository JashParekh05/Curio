-- Adaptive Learning Game migration (Phase 2, Req 16) -- ADDITIVE STEP ONLY.
-- Run this in the Supabase SQL editor (Dashboard -> SQL Editor).
--
-- Run MANUALLY by the operator, never at application startup. This file creates
-- the three new Phase 2 tables that back account-linked game sessions, per-node
-- progress + decay scheduling, and per-quiz attempt history. The tables are
-- accessed best-effort through db/supabase.get_client(), each call wrapped in
-- try/except; a Supabase failure is logged and never blocks the loop
-- (Req 16.2, 16.3).
--
-- ADDITIVE ONLY: every statement is idempotent (create table if not exists), so
-- re-running is safe. NO existing table is altered, and NO Supabase table or
-- data is dropped as part of this feature.

-- 1. game_sessions: one row per started session.
create table if not exists game_sessions (
  session_id   uuid primary key,
  user_id      uuid not null,            -- from require_user (Req 17.1)
  goal         text not null,
  floor_node   text,
  created_at   timestamptz default now(),
  updated_at   timestamptz default now()
);

-- 2. game_progress: per-node state + decay schedule.
create table if not exists game_progress (
  id              bigint generated always as identity primary key,
  session_id      uuid references game_sessions(session_id),
  user_id         uuid not null,
  node            text not null,
  state           text not null,         -- LOCKED|NEXT|CLEARED|DECAYING|MASTERED
  decay_due_at    timestamptz,           -- Decay_Schedule 1d->3d->7d->16d (Req 14)
  decay_step      int default 0,
  updated_at      timestamptz default now(),
  unique (session_id, node)
);

-- 3. attempts: per-quiz attempt history.
create table if not exists attempts (
  id            bigint generated always as identity primary key,
  session_id    uuid references game_sessions(session_id),
  user_id       uuid not null,
  node          text not null,
  band          text not null,
  score_pct     numeric not null,
  missed        text[] default '{}',
  correct       text[] default '{}',
  created_at    timestamptz default now()
);
