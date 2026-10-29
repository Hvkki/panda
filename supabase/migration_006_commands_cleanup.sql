-- The app <-> worker relay rows (`commands`) were never deleted. The Helpers panel polled GET /api/spawn
-- every 5 s, each answer a ~16 KB row: 6 500 rows / 112 MB in three days, and the database (free tier)
-- stopped answering on 29 October. Finished rows are only read by the app within seconds, so drop them
-- after 30 minutes. Applied on 29 October through the management API. Safe to run again.
select cron.unschedule(jobid) from cron.job where jobname = 'panda-commands-cleanup';
select cron.schedule('panda-commands-cleanup', '*/15 * * * *',
  $$delete from public.commands where status in ('done','error','expired') and created_at < now() - interval '30 minutes'$$);
