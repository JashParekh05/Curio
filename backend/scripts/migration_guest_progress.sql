-- migration_guest_progress.sql
--
-- Server-side guest clip counter for the soft/hard signup gate.
--
-- Replaces (well, backs) the per-device localStorage counter in
-- frontend/lib/guest-progress.ts, which is trivially bypassable (incognito /
-- clearing storage) and does not follow the guest across devices. The count is
-- keyed on the (anonymous) Supabase auth user_id, so:
--   * clearing localStorage no longer resets it (the server remembers),
--   * it follows the same anonymous identity across devices/tabs.
-- The client keeps localStorage as an optimistic/offline fallback and reconciles
-- to max(local, server), so nothing breaks if this table is absent.
--
-- Idempotent: safe to re-run. Apply in the Supabase SQL editor.

create table if not exists guest_progress (
  user_id        uuid primary key,
  clips_watched  int  not null default 0,
  gate_dismissed boolean not null default false,
  updated_at     timestamptz not null default now()
);

-- Atomic upsert + increment; returns the new clip count. Mirrors the pattern of
-- increment_quota_usage in migration_cold_start.sql. Called by the backend with
-- the service key, so no row-level grants are required.
create or replace function increment_guest_clips(p_user_id uuid)
returns int language plpgsql as $$
declare
  v_count int;
begin
  insert into guest_progress (user_id, clips_watched, updated_at)
    values (p_user_id, 1, now())
  on conflict (user_id) do update
    set clips_watched = guest_progress.clips_watched + 1,
        updated_at = now()
  returning clips_watched into v_count;
  return v_count;
end;
$$;
