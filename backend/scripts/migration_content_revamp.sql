-- Content retrieval revamp migration (Phase 4, Req 8) -- ADDITIVE STEP ONLY.
-- Run this in the Supabase SQL editor (Dashboard -> SQL Editor).
--
-- Run MANUALLY by the operator, never at application startup (Req 8.2) and only
-- after a Pre_Migration_Snapshot is recorded (Req 8.11). This file is the
-- ADDITIVE step of the content-retrieval-revamp Staged_Migration (Req 8.1): it
-- only creates new tables and adds new nullable/defaulted columns. It leaves
-- every existing row in topics/clips unchanged (Req 8.7, 8.8) and adds no
-- destructive change -- the cutover and cleanup steps are applied separately,
-- later, gated by the Migration_Gate.
--
-- Every statement is idempotent (create table if not exists / add column if not
-- exists), so re-running is safe. The matching reverse step (recorded by the
-- staged_migration runner) drops exactly the objects created here.
--
-- Column names and types align with the shells that read/write them:
--   canonical_arc            <- arc_unifier_store.py, coverage_view_store.py
--   curriculum_spine_nodes   <- curriculum_spine_store.py
--   curriculum_spine_edges   <- curriculum_spine_store.py
--   takeaway_artifacts       <- takeaway_store.py
--   migration_state          <- staged_migration.py
--   topics.archived          <- arc_backfill_runner.py, *_store.py (Pruned_Topic)
--   topics.arc_complete      <- arc_backfill_runner.py, curriculum_spine_store.py

-- 1. Canonical_Arc -- the single unified arc representation, one row per role,
--    keyed by (topic_slug, ordinal). Supersedes topic_sections +
--    topic_arcs/topic_arc_roles. Ordinals are consecutive from 1 (enforced by the
--    Arc_Unifier core, Req 1.1). (Req 8.1, 8.7)
create table if not exists canonical_arc (
  topic_slug         text not null,                  -- FK -> topics.slug
  ordinal            int  not null check (ordinal >= 1),
  role               text not null,                  -- a PedagogicalRole value
  superseded_legacy  boolean not null default false, -- true when role model won over legacy (Req 1.5)
  updated_at         timestamptz default now(),
  primary key (topic_slug, ordinal)
);
create index if not exists canonical_arc_topic_idx on canonical_arc (topic_slug);

-- 2. Curriculum_Spine nodes -- one stable Topic per slug (Req 5.1, 5.5). The
--    Pruned_Topic exclusion is driven by topics.archived (see step 6), so no
--    archived flag is duplicated here.
create table if not exists curriculum_spine_nodes (
  topic_slug     text primary key,                   -- at most one node per slug (Req 5.5)
  content_level  text not null,                       -- derived Content_Level (Req 5.1)
  est_minutes    int  not null default 0 check (est_minutes >= 0),  -- feeds Placement (Req 9.3)
  created_at     timestamptz default now(),
  updated_at     timestamptz default now()
);

-- 3. Curriculum_Spine edges -- directed prerequisite relationships A -> B meaning
--    A is a prerequisite of B (Req 5.2). The graph is kept acyclic by the pure
--    add_edge core (Req 5.3); the DB stores whatever the core admits.
create table if not exists curriculum_spine_edges (
  prerequisite  text not null,                        -- source topic slug (FK -> curriculum_spine_nodes.topic_slug)
  dependent     text not null,                        -- target topic slug (FK -> curriculum_spine_nodes.topic_slug)
  created_at    timestamptz default now(),
  primary key (prerequisite, dependent)
);
create index if not exists curriculum_spine_edges_dependent_idx
  on curriculum_spine_edges (dependent);

-- 4. Takeaway_Artifacts -- a per-(learner, topic) summary the learner keeps,
--    unique per pair (Req 10.1, 10.6), surviving restart (Req 10.3).
create table if not exists takeaway_artifacts (
  learner_id   text not null,
  topic_slug   text not null,
  points       jsonb not null,                        -- derived arc-ordered summary points (Req 10.2)
  created_at   timestamptz default now(),
  primary key (learner_id, topic_slug)
);

-- 5. Migration_State -- staged-migration tracking, one row per (migration_id,
--    step). Records the recorded reverse step and the Pre_Migration_Snapshot
--    reference the gate requires (Req 8.9, 8.11). applied_at is null until the
--    step is applied.
create table if not exists migration_state (
  migration_id  text not null,
  step          text not null check (step in ('additive', 'cutover', 'cleanup')),
  applied_at    timestamptz,                           -- null until applied
  reverse_sql   text not null,                          -- recorded reverse step (Req 8.1, 8.3)
  snapshot_ref  text,                                   -- Pre_Migration_Snapshot reference (Req 8.11)
  updated_at    timestamptz default now(),
  primary key (migration_id, step)
);

-- 6. Reversible additive flags on topics. Both are nullable/defaulted so every
--    existing topics row is left unchanged (Req 8.7) and pre-feature rows read
--    back as non-pruned and not-arc-complete (no backfill required).
--    - archived:     marks a Pruned_Topic, excluded from the spine and feed
--                    routing; reversible (a restore clears it). (Req 3.6, 5.7)
--    - arc_complete: set by the backfill shell when every Canonical_Arc role is
--                    filled. (Req 3.4)
alter table topics add column if not exists archived     boolean not null default false;
alter table topics add column if not exists arc_complete boolean not null default false;
create index if not exists topics_archived_idx     on topics (archived);
create index if not exists topics_arc_complete_idx on topics (arc_complete);
