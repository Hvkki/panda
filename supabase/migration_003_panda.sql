-- Panda phases 2 + 3: files, projects, commands relay, API-key vault, push.
-- Run once in the Supabase SQL editor after migration_002. Safe to re-run.

-- ── Projects ────────────────────────────────────────────────────────────────
create table if not exists public.projects (
  id uuid primary key default gen_random_uuid(),
  owner uuid not null default auth.uid() references auth.users (id) on delete cascade,
  name text not null default 'New project',
  description text not null default '',
  instructions text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
alter table public.chats add column if not exists project_id uuid references public.projects (id) on delete set null;

-- ── Files (agent outputs, your uploads, project files) ─────────────────────
create table if not exists public.files (
  id uuid primary key default gen_random_uuid(),
  owner uuid not null default auth.uid() references auth.users (id) on delete cascade,
  chat_id uuid references public.chats (id) on delete cascade,
  message_id bigint references public.messages (id) on delete set null,
  project_id uuid references public.projects (id) on delete cascade,
  kind text not null default 'output' check (kind in ('output', 'upload', 'project')),
  name text not null,
  path text not null,            -- object path in the panda-files bucket: <owner>/...
  mime text not null default 'application/octet-stream',
  size bigint not null default 0,
  pinned boolean not null default false,
  created_at timestamptz not null default now()
);
create index if not exists files_owner_idx on public.files (owner, created_at desc);

-- ── Commands: app -> worker requests (skills, connectors, crons...) ────────
create table if not exists public.commands (
  id bigint generated always as identity primary key,
  owner uuid not null default auth.uid() references auth.users (id) on delete cascade,
  op text not null,
  args jsonb not null default '{}'::jsonb,
  status text not null default 'pending' check (status in ('pending', 'running', 'done', 'error')),
  result jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists commands_pending_idx on public.commands (status, id) where status = 'pending';

-- ── Push subscriptions (phone notifications when the app is closed) ────────
create table if not exists public.push_subscriptions (
  endpoint text primary key,
  owner uuid not null default auth.uid() references auth.users (id) on delete cascade,
  keys jsonb not null,
  created_at timestamptz not null default now()
);

do $$
declare t text;
begin
  foreach t in array array['projects', 'files', 'commands', 'push_subscriptions'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists owner_all on public.%I', t);
    execute format('create policy owner_all on public.%I for all to authenticated
                    using (owner = auth.uid()) with check (owner = auth.uid())', t);
    execute format('drop policy if exists worker_all on public.%I', t);
    execute format('create policy worker_all on public.%I for all to authenticated
                    using (public.is_relay_worker()) with check (public.is_relay_worker())', t);
  end loop;
  foreach t in array array['projects', 'files', 'commands'] loop
    if not exists (select 1 from pg_publication_tables
                   where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = t) then
      execute format('alter publication supabase_realtime add table public.%I', t);
    end if;
  end loop;
end $$;
alter table public.commands replica identity full;

drop trigger if exists projects_touch on public.projects;
create trigger projects_touch before update on public.projects
  for each row execute function public.touch_updated_at();
drop trigger if exists commands_touch on public.commands;
create trigger commands_touch before update on public.commands
  for each row execute function public.touch_updated_at();

-- ── API-key vault: you can add/replace/delete keys and see their NAMES, never
--    their values. Only the worker can read values (it gives them to the agent).
create table if not exists public.secrets (
  owner uuid not null references auth.users (id) on delete cascade,
  name text not null check (name ~ '^[A-Z][A-Z0-9_]{1,63}$'),
  value text not null,
  note text not null default '',
  updated_at timestamptz not null default now(),
  primary key (owner, name)
);
alter table public.secrets enable row level security;
drop policy if exists worker_read on public.secrets;
create policy worker_read on public.secrets for select to authenticated using (public.is_relay_worker());

create or replace function public.set_secret(p_name text, p_value text, p_note text default '')
returns void language plpgsql security definer set search_path = public as $$
begin
  if auth.uid() is null then raise exception 'not signed in'; end if;
  insert into public.secrets (owner, name, value, note) values (auth.uid(), upper(p_name), p_value, coalesce(p_note, ''))
  on conflict (owner, name) do update set value = excluded.value, note = excluded.note, updated_at = now();
end $$;

create or replace function public.delete_secret(p_name text)
returns void language sql security definer set search_path = public as $$
  delete from public.secrets where owner = auth.uid() and name = upper(p_name);
$$;

create or replace function public.list_secrets()
returns table (name text, note text, updated_at timestamptz)
language sql stable security definer set search_path = public as $$
  select name, note, updated_at from public.secrets where owner = auth.uid() order by name;
$$;

revoke all on function public.set_secret(text, text, text) from public;
revoke all on function public.delete_secret(text) from public;
revoke all on function public.list_secrets() from public;
grant execute on function public.set_secret(text, text, text) to authenticated;
grant execute on function public.delete_secret(text) to authenticated;
grant execute on function public.list_secrets() to authenticated;

-- ── Storage: files bucket, each user owns the folder named by their id ─────
insert into storage.buckets (id, name, public, file_size_limit)
values ('panda-files', 'panda-files', false, 52428800)
on conflict (id) do nothing;

drop policy if exists panda_files_owner on storage.objects;
create policy panda_files_owner on storage.objects for all to authenticated
  using (bucket_id = 'panda-files' and (storage.foldername(name))[1] = auth.uid()::text)
  with check (bucket_id = 'panda-files' and (storage.foldername(name))[1] = auth.uid()::text);
drop policy if exists panda_files_worker on storage.objects;
create policy panda_files_worker on storage.objects for all to authenticated
  using (bucket_id = 'panda-files' and public.is_relay_worker())
  with check (bucket_id = 'panda-files' and public.is_relay_worker());

-- Only you may sign in: block self-signup at the database level too.
create or replace function public.block_signups()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  -- worker + tester + your account + one spare (the new Gmail you plan to add)
  if (select count(*) from auth.users) >= 5 then
    raise exception 'sign-ups are closed';
  end if;
  return new;
end $$;
drop trigger if exists block_signups on auth.users;
create trigger block_signups before insert on auth.users
  for each row execute function public.block_signups();
