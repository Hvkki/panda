-- Embra app, phase 1: chat options, thinking, approvals, settings, worker code storage.
-- Run once in the Supabase SQL editor after schema.sql. Safe to re-run.

alter table public.chats add column if not exists pinned boolean not null default false;
alter table public.chats add column if not exists model text not null default 'claude-sonnet-5.5';
alter table public.chats add column if not exists effort text not null default 'medium';
alter table public.chats add column if not exists archived boolean not null default false;

-- Thinking is stored apart from the answer, so the app can hide it unless toggled on.
alter table public.messages add column if not exists thinking text not null default '';

-- "Stop generating": the app sets cancel=true on the streaming reply.
alter table public.messages add column if not exists cancel boolean not null default false;

-- Approval cards: the worker writes role='tool', status='pending', meta.request_id;
-- the app answers by setting decision; the worker reads it and resumes the turn.
alter table public.messages add column if not exists decision text
  check (decision in ('approved', 'rejected', 'trust'));
alter table public.messages drop constraint if exists messages_status_check;
alter table public.messages add constraint messages_status_check
  check (status in ('pending', 'processing', 'streaming', 'done', 'error', 'waiting'));

-- Per-user settings (theme, show_thinking, approvals mode, default model...).
create table if not exists public.user_settings (
  owner uuid primary key default auth.uid() references auth.users (id) on delete cascade,
  settings jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);
alter table public.user_settings enable row level security;
drop policy if exists owner_all on public.user_settings;
create policy owner_all on public.user_settings for all to authenticated
  using (owner = auth.uid()) with check (owner = auth.uid());
drop policy if exists worker_read on public.user_settings;
create policy worker_read on public.user_settings for select to authenticated
  using (public.is_relay_worker());

create index if not exists chats_owner_idx on public.chats (owner, pinned desc, updated_at desc);

-- Bump a chat to the top of the list whenever it gets a new message.
create or replace function public.bump_chat()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  update public.chats set updated_at = now() where id = new.chat_id;
  return new;
end $$;
drop trigger if exists messages_bump_chat on public.messages;
create trigger messages_bump_chat after insert on public.messages
  for each row execute function public.bump_chat();

do $$
begin
  if not exists (select 1 from pg_publication_tables
                 where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'user_settings') then
    alter publication supabase_realtime add table public.user_settings;
  end if;
end $$;

-- Realtime must send full rows on update so streaming text reaches the app.
alter table public.messages replica identity full;
alter table public.worker_sessions replica identity full;
