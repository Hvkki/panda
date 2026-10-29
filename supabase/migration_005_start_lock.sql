-- B2 (red-team): two notebooks could boot for one account. The `session` function checked "is any run
-- alive?" and then inserted a 'requested' row as two separate steps, so two overlapping calls (a watchdog
-- tick racing a Start, two watchdog ticks, a watchdog racing a hand-off) each saw "nothing alive" and each
-- launched. Two gateways on one Kiro key then wrote the same Storage blobs and could corrupt the restore
-- set. This makes the check-and-insert ATOMIC per owner with a Postgres advisory lock, so the second
-- caller sees the first's fresh 'requested' row and backs off.
--
-- The hand-off overlap is preserved: a hand-off intentionally starts a successor WHILE the old run is
-- still alive, so it passes the old run's id as p_exclude and the alive-check ignores exactly that row.
-- Run once in the Supabase SQL editor. Safe to run again (create or replace). MUST be applied BEFORE the
-- matching session function is deployed (the function calls this RPC).

create or replace function public.start_session(
  p_owner uuid, p_info jsonb, p_kernel text, p_exclude uuid default null
) returns public.worker_sessions
language plpgsql
security definer
set search_path = public
as $$
declare
  r public.worker_sessions;
begin
  -- One launcher per owner at a time; released at transaction end.
  perform pg_advisory_xact_lock(hashtext(p_owner::text));
  -- Refuse if another run for this owner is already alive, ignoring the hand-off predecessor
  -- (p_exclude). Mirrors isAlive() in the edge function: a running/stopping row with a fresh heartbeat,
  -- or a just-requested/starting row still within its boot window or heartbeating while it waits.
  if exists (
    select 1 from public.worker_sessions s
    where s.owner = p_owner
      and (p_exclude is null or s.id <> p_exclude)
      and (
        (s.status in ('running', 'stopping') and s.heartbeat_at > now() - interval '90 seconds')
        or (s.status in ('requested', 'starting')
            and (s.started_at > now() - interval '900 seconds'
                 or s.heartbeat_at > now() - interval '90 seconds'))
      )
  ) then
    return null;      -- someone is alive / already starting; the caller backs off
  end if;
  insert into public.worker_sessions (owner, status, info, kaggle_kernel)
  values (p_owner, 'requested', p_info, p_kernel)
  returning * into r;
  return r;
end;
$$;

revoke all on function public.start_session(uuid, jsonb, text, uuid) from anon, authenticated;
grant execute on function public.start_session(uuid, jsonb, text, uuid) to service_role;
notify pgrst, 'reload schema';

-- To roll back: drop function public.start_session(uuid, jsonb, text, uuid);
-- (and redeploy the previous session function, which inserts directly).
