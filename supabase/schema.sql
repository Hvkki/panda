-- Kiro Crew relay schema: phone app <-> Supabase <-> Kaggle worker.
-- Run once in the Supabase SQL editor. Safe to re-run.

create extension if not exists pgcrypto;

-- Users allowed to act as the Kaggle worker. No RLS policies: only the
-- service role (secret key) can read or change this table.
create table if not exists public.relay_workers (
  user_id uuid primary key references auth.users (id) on delete cascade,
  created_at timestamptz not null default now()
);
alter table public.relay_workers enable row level security;

create or replace function public.is_relay_worker()
returns boolean language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.relay_workers where user_id = auth.uid());
$$;
revoke all on function public.is_relay_worker() from public;
grant execute on function public.is_relay_worker() to authenticated;

-- One row per conversation. slot_id is the Kiro Crew chat slot it maps to.
create table if not exists public.chats (
  id uuid primary key default gen_random_uuid(),
  owner uuid not null default auth.uid() references auth.users (id) on delete cascade,
  title text not null default 'New chat',
  agent text not null default '',
  slot_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Messages. The app inserts role=user/status=pending; the worker claims it,
-- inserts the assistant row and streams into its content.
create table if not exists public.messages (
  id bigint generated always as identity primary key,
  chat_id uuid not null references public.chats (id) on delete cascade,
  owner uuid not null references auth.users (id) on delete cascade,
  role text not null check (role in ('user', 'assistant', 'system', 'tool')),
  content text not null default '',
  status text not null default 'done'
    check (status in ('pending', 'processing', 'streaming', 'done', 'error')),
  reply_to bigint references public.messages (id) on delete set null,
  meta jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists messages_pending_idx on public.messages (status, created_at)
  where status = 'pending';
create index if not exists messages_chat_idx on public.messages (chat_id, id);

-- One row per Kaggle run. The app sets stop_requested; the worker heartbeats.
create table if not exists public.worker_sessions (
  id uuid primary key default gen_random_uuid(),
  owner uuid references auth.users (id) on delete cascade,
  status text not null default 'starting'
    check (status in ('requested', 'starting', 'running', 'stopping', 'stopped', 'error')),
  stop_requested boolean not null default false,
  kaggle_kernel text,
  started_at timestamptz not null default now(),
  heartbeat_at timestamptz,
  stopped_at timestamptz,
  last_snapshot text,
  info jsonb not null default '{}'::jsonb
);

-- Notifications from crons / background work, mirrored by the worker.
create table if not exists public.notifications (
  id bigint generated always as identity primary key,
  owner uuid references auth.users (id) on delete cascade,
  source text not null default 'crew',
  text text not null,
  meta jsonb not null default '{}'::jsonb,
  read boolean not null default false,
  created_at timestamptz not null default now()
);

-- Keep messages.owner equal to the chat's owner, whoever inserts.
create or replace function public.messages_set_owner()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  select owner into new.owner from public.chats where id = new.chat_id;
  if new.owner is null then
    raise exception 'unknown chat %', new.chat_id;
  end if;
  return new;
end $$;
drop trigger if exists messages_owner on public.messages;
create trigger messages_owner before insert on public.messages
  for each row execute function public.messages_set_owner();

create or replace function public.touch_updated_at()
returns trigger language plpgsql as $$
begin new.updated_at = now(); return new; end $$;
drop trigger if exists chats_touch on public.chats;
create trigger chats_touch before update on public.chats
  for each row execute function public.touch_updated_at();
drop trigger if exists messages_touch on public.messages;
create trigger messages_touch before update on public.messages
  for each row execute function public.touch_updated_at();

-- Row-level security: owners see their own rows; the worker sees relay rows.
alter table public.chats enable row level security;
alter table public.messages enable row level security;
alter table public.worker_sessions enable row level security;
alter table public.notifications enable row level security;

do $$
declare t text;
begin
  foreach t in array array['chats', 'messages', 'worker_sessions', 'notifications'] loop
    execute format('drop policy if exists owner_all on public.%I', t);
    execute format('create policy owner_all on public.%I for all to authenticated
                    using (owner = auth.uid()) with check (owner = auth.uid())', t);
    execute format('drop policy if exists worker_all on public.%I', t);
    execute format('create policy worker_all on public.%I for all to authenticated
                    using (public.is_relay_worker()) with check (public.is_relay_worker())', t);
  end loop;
end $$;

-- Live updates for the app (streaming replies, session status, notifications).
do $$
declare t text;
begin
  foreach t in array array['chats', 'messages', 'worker_sessions', 'notifications'] loop
    if not exists (select 1 from pg_publication_tables
                   where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = t) then
      execute format('alter publication supabase_realtime add table public.%I', t);
    end if;
  end loop;
end $$;

-- Private bucket for Kiro Crew snapshots; only the worker can use it.
insert into storage.buckets (id, name, public)
values ('kirocrew-state', 'kirocrew-state', false)
on conflict (id) do nothing;

drop policy if exists kirocrew_state_worker on storage.objects;
create policy kirocrew_state_worker on storage.objects for all to authenticated
  using (bucket_id = 'kirocrew-state' and public.is_relay_worker())
  with check (bucket_id = 'kirocrew-state' and public.is_relay_worker());
