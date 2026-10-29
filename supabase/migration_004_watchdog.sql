-- Watchdog: every 5 minutes Supabase itself checks that each account's Panda is alive and starts it again
-- when it died without a Stop (Kaggle killed the machine, the worker crashed). The work is done by the
-- `session` edge function (watchdog()); this only schedules the call.
-- Run once in the Supabase SQL editor. Safe to run again.

create extension if not exists pg_cron;
create extension if not exists pg_net;
create extension if not exists pgcrypto;

-- Server-only values. RLS on and no policies: only the service role (the edge function) can read it.
create table if not exists public.panda_private (
  key text primary key,
  value text not null,
  created_at timestamptz not null default now()
);
alter table public.panda_private enable row level security;
revoke all on public.panda_private from anon, authenticated;

-- A random key made here, never typed or shown anywhere: the cron call sends it, the function checks it.
insert into public.panda_private (key, value)
values ('watchdog_key', encode(extensions.gen_random_bytes(32), 'hex'))
on conflict (key) do nothing;

-- The project URL. The PUBLISHABLE key row ('publishable_key', public by design; it only gets the call
-- past the gateway) is written separately with the service key, so no key is ever typed here or in git.
insert into public.panda_private (key, value) values
  ('project_url', 'https://ufvskhagspdjslomzwjh.supabase.co')
on conflict (key) do update set value = excluded.value;
notify pgrst, 'reload schema';

select cron.unschedule(jobid) from cron.job where jobname = 'panda-watchdog';
select cron.schedule('panda-watchdog', '*/5 * * * *', $$
  select net.http_post(
    url := (select value from public.panda_private where key = 'project_url') || '/functions/v1/session',
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'apikey', (select value from public.panda_private where key = 'publishable_key'),
      'Authorization', 'Bearer ' || (select value from public.panda_private where key = 'publishable_key'),
      'x-watchdog-key', (select value from public.panda_private where key = 'watchdog_key')),
    body := '{}'::jsonb,
    timeout_milliseconds := 60000);
$$);

-- To turn it off:  select cron.unschedule('panda-watchdog');
