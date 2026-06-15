-- Personal/relative skip thresholds: store a per-user EMA of watch ratio so a
-- "skip" can be judged relative to how that user normally watches, rather than
-- against global absolute cutoffs.
--
-- Run once against the Supabase project (SQL editor), like the other migrations.

alter table user_profiles add column if not exists avg_watch_ratio float;

-- Atomic EMA update, mirroring merge_user_taste in migration_pgvector.sql
-- (select ... for update + insert ... on conflict do update).
create or replace function merge_user_watch_ratio(p_user_id text, p_watch_ratio float, p_alpha float default 0.1)
returns void language plpgsql as $$
declare
  existing float;
  merged float;
begin
  select avg_watch_ratio into existing from user_profiles where user_id = p_user_id for update;
  if existing is null then
    merged := p_watch_ratio;
  else
    merged := (1 - p_alpha) * existing + p_alpha * p_watch_ratio;
  end if;
  insert into user_profiles (user_id, avg_watch_ratio) values (p_user_id, merged)
    on conflict (user_id) do update set avg_watch_ratio = excluded.avg_watch_ratio;
end; $$;
