-- Keep the free database far from overload, and warn the owner before it gets there (29 October 2026).
-- The relay table filled the database that day (migration_006 cleans it now); measuring afterwards showed
-- Realtime decoding every change of eight tables (its query was the database's top cost) and nothing
-- trimming pg_cron's run log. Applied through the Management API. Safe to run again.
--
-- The owner to warn is one row in panda_private ('health_owner'), written separately at apply time
-- with the owner's user id, so no id is typed into git:
--   insert into public.panda_private (key, value) values ('health_owner', '<owner uuid>')
--   on conflict (key) do update set value = excluded.value;

-- 1. Realtime decodes every change of every table in its publication. The app subscribes only to
--    chats, messages, notifications and worker_sessions; the rest (the relay's ~20 KB answer rows above
--    all) was decoded for no one.
do $$
declare t text;
begin
  foreach t in array array['commands', 'files', 'projects', 'user_settings'] loop
    if exists (select 1 from pg_publication_tables
               where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = t) then
      execute format('alter publication supabase_realtime drop table public.%I', t);
    end if;
  end loop;
end $$;
-- Nothing listens to commands any more, so its WAL no longer needs the whole old row of each update.
alter table public.commands replica identity default;

-- 2. Housekeeping, daily: pg_cron keeps one row per run forever (the watchdog runs every 5 minutes), and
--    relay rows left pending or running (Panda was off) were never deleted.
select cron.unschedule(jobid) from cron.job where jobname = 'panda-housekeeping';
select cron.schedule('panda-housekeeping', '17 3 * * *', $$
  delete from cron.job_run_details where end_time < now() - interval '3 days';
  delete from public.commands where created_at < now() - interval '1 day';
$$);

-- 3. Early warning, every 30 minutes: when the database, one table, the relay or file storage passes a
--    safe line, the owner gets a bell notification (at most once a day for the same findings). A flooded
--    relay is trimmed at once instead of waiting for the 15-minute cleanup.
create or replace function public.panda_db_health() returns text
language plpgsql security definer set search_path = public, pg_catalog as $$
declare
  owner_id uuid := (select value::uuid from public.panda_private where key = 'health_owner');
  db_mb numeric := pg_database_size(current_database()) / 1048576.0;
  cmd_n bigint;
  store_mb numeric;
  big text;
  msg text := '';
  kinds text[] := '{}';
begin
  select count(*) into cmd_n from public.commands;
  if cmd_n > 3000 then
    delete from public.commands
      where status in ('done', 'error', 'expired') and created_at < now() - interval '2 minutes';
    msg := msg || format('The app-to-Panda relay had %s rows, so the finished ones were deleted early. ', cmd_n);
    kinds := kinds || 'relay'::text;
  end if;
  if db_mb > 300 then
    msg := msg || format('The database is %s MB; the free plan allows 500 MB. ', round(db_mb));
    kinds := kinds || 'db'::text;
  end if;
  select string_agg(format('%s (%s MB)', relname, round(pg_total_relation_size(relid) / 1048576.0)), ', ')
    into big from pg_stat_user_tables
    where schemaname = 'public' and pg_total_relation_size(relid) > 50 * 1048576;
  if big is not null then
    msg := msg || 'Large tables: ' || big || '. ';
    kinds := kinds || 'table'::text;
  end if;
  select coalesce(sum((metadata->>'size')::bigint), 0) / 1048576.0 into store_mb from storage.objects;
  if store_mb > 900 then
    msg := msg || format('File storage holds %s MB; the free plan includes 1 GB, and Supabase can block '
                         'a project that stays over its quota. ', round(store_mb));
    kinds := kinds || 'storage'::text;
  end if;
  if msg = '' then
    return 'ok';
  end if;
  if owner_id is null then
    return 'no health_owner set: ' || msg;
  end if;
  if exists (select 1 from public.notifications
             where owner = owner_id and source = 'panda' and meta->>'kind' = 'db-health'
               and meta->>'kinds' = array_to_string(kinds, ',') and created_at > now() - interval '1 day') then
    return 'already told today: ' || msg;
  end if;
  insert into public.notifications (owner, source, text, meta)
  values (owner_id, 'panda', trim(msg),
          jsonb_build_object('kind', 'db-health', 'kinds', array_to_string(kinds, ','),
                             'title', 'Supabase usage warning'));
  return 'told: ' || msg;
end $$;
revoke all on function public.panda_db_health() from public, anon, authenticated;
select cron.unschedule(jobid) from cron.job where jobname = 'panda-db-health';
select cron.schedule('panda-db-health', '*/30 * * * *', $$select public.panda_db_health()$$);
