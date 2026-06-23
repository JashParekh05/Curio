-- Durable self-heal attempt tracking (cost-control safeguard).
--
-- The path feed re-triggers background ingestion for any empty, non-generating
-- topic. The per-slug attempt cap that bounds this loop (self_heal_state) lived
-- ONLY in process memory, so it reset on every restart/deploy and was not shared
-- across workers. A topic that genuinely cannot be filled (no captions, niche
-- query) therefore re-burned paid TranscriptAPI credits + YouTube quota a fresh
-- N attempts after every deploy, forever.
--
-- This table persists the attempt count + last-attempt time per topic so the cap
-- survives restarts and is shared across workers. It is read by the self-heal
-- decision (should_self_heal / is_terminal_failed) and written when an empty
-- generation attempt finishes; a successful run (>= 1 clip) deletes the row.
--
-- Additive and idempotent: creates one new table, touches nothing existing.

create table if not exists self_heal_attempts (
  topic_slug text primary key,
  attempts int not null default 0,
  last_attempt_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
