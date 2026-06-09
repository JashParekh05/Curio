-- Post-clip quiz support.
-- clip_quizzes caches the one LLM-generated comprehension question per clip
-- so we only pay for generation once per clip, not once per viewer.
create table if not exists clip_quizzes (
  clip_id uuid primary key references clips(id) on delete cascade,
  question text not null,
  options jsonb not null,
  correct_index int not null,
  explanation text,
  created_at timestamptz default now()
);

-- Quiz outcome telemetry on the existing events table. Null = event wasn't a
-- quiz answer. The API degrades gracefully if this column is missing.
alter table clip_events add column if not exists quiz_correct boolean;
