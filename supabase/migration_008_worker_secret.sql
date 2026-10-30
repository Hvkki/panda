-- migration_008: let Panda's worker save an API key into the owner's vault (agent issue #41).
-- Before this, only the owner (set_secret, auth.uid()) could write the vault; agents could only write
-- ~/.panda/secrets.env, which every start rewrites from the vault, so uploaded keys vanished at each
-- restart. This function is callable only by a relay worker (it already READS the vault), never sets a
-- Developer variable (DEV__...), never targets another worker account, and keeps an existing key unless
-- p_replace is true. Additive: no table or policy changes.

create or replace function public.worker_set_secret(p_owner uuid, p_name text, p_value text,
                                                    p_note text default '', p_replace boolean default false)
returns text language plpgsql security definer set search_path = public as $$
declare
  v_name text := upper(coalesce(p_name, ''));
  v_had boolean;
begin
  if not public.is_relay_worker() then raise exception 'only Panda''s worker may call this'; end if;
  if v_name like 'DEV\_\_%' escape '\' then raise exception 'Developer variables are set only in the app'; end if;
  if v_name !~ '^[A-Z][A-Z0-9_]{1,63}$' then raise exception 'not a valid key name'; end if;
  if p_value is null or length(p_value) = 0 or length(p_value) > 16384 then raise exception 'not a valid key value'; end if;
  if not exists (select 1 from auth.users where id = p_owner) then raise exception 'unknown owner'; end if;
  if exists (select 1 from public.relay_workers where user_id = p_owner) then raise exception 'not an owner account'; end if;
  select exists (select 1 from public.secrets where owner = p_owner and name = v_name) into v_had;
  if v_had and not coalesce(p_replace, false) then return 'exists'; end if;
  insert into public.secrets (owner, name, value, note)
  values (p_owner, v_name, p_value, left(coalesce(p_note, ''), 200))
  on conflict (owner, name) do update set value = excluded.value, note = excluded.note, updated_at = now();
  return case when v_had then 'replaced' else 'saved' end;
end $$;

revoke all on function public.worker_set_secret(uuid, text, text, text, boolean) from public;
revoke all on function public.worker_set_secret(uuid, text, text, text, boolean) from anon;
grant execute on function public.worker_set_secret(uuid, text, text, text, boolean) to authenticated;
