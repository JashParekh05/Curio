-- Row-Level Security lockdown
-- Run this in Supabase SQL editor (Dashboard -> SQL Editor)
--
-- WHY: the frontend ships the public anon/publishable key, so anyone can call
-- Supabase PostgREST directly and bypass the backend's ownership checks. The
-- backend uses the SERVICE-ROLE key, which bypasses RLS -- so enabling RLS with
-- NO policies denies all direct anon/authenticated table access while the
-- backend keeps full access. The frontend never queries these tables directly
-- (it only uses supabase.auth.*), so this is a zero-impact lockdown.
--
-- Safe to run repeatedly. After running, verify in Table Editor that every
-- table shows "RLS enabled".

-- Enable RLS on every application table. With no CREATE POLICY statements,
-- the default is deny-all for anon + authenticated roles.
alter table user_profiles      enable row level security;
alter table learning_paths     enable row level security;
alter table clips              enable row level security;
alter table clip_events        enable row level security;
alter table session_embeddings enable row level security;
alter table topics             enable row level security;
alter table topic_sections     enable row level security;
alter table quiz_questions     enable row level security;
alter table quiz_results       enable row level security;

-- Defense in depth: force RLS even for the table owner role, so a misconfigured
-- role can't accidentally bypass it. (The service-role key still bypasses RLS;
-- this only hardens against owner-role access via PostgREST.)
alter table user_profiles      force row level security;
alter table learning_paths     force row level security;
alter table clip_events        force row level security;
alter table session_embeddings force row level security;
alter table quiz_results       force row level security;

-- NOTE: if you ever want the frontend to read PUBLIC content (e.g. browse
-- topics/clips without going through the API), add narrow read-only policies
-- for just those tables, e.g.:
--
--   create policy "public read topics" on topics
--     for select to anon, authenticated using (true);
--
-- Do NOT add blanket policies to user_profiles / learning_paths / clip_events /
-- quiz_results -- those must stay backend-only or be scoped to auth.uid().
