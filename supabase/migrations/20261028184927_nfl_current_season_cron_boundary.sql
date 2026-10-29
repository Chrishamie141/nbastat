-- Least-privilege boundary for the staging app role. The role can install only
-- this fixed job; it receives no direct Cron or Vault schema privileges.
begin;

create or replace function public.install_nfl_data_refresh_cron(
  base_url text,
  bearer_secret text
) returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public, cron, vault
as $$
declare
  secret_id uuid;
  prior_job bigint;
  installed_job bigint;
  target text;
  command text;
begin
  if base_url !~ '^https://[a-zA-Z0-9-]+\.vercel\.app/?$' then
    raise exception 'invalid staging Vercel origin';
  end if;
  if length(bearer_secret) < 32 then
    raise exception 'invalid refresh bearer';
  end if;
  target := rtrim(base_url, '/') || '/api/cron/nfl-data-refresh';

  select id into secret_id from vault.secrets
   where name = 'nfl_data_refresh_cron_bearer';
  if secret_id is null then
    perform vault.create_secret(
      bearer_secret, 'nfl_data_refresh_cron_bearer',
      'Bearer for staging NFL current-season refresh'
    );
  else
    perform vault.update_secret(
      secret_id, bearer_secret, 'nfl_data_refresh_cron_bearer',
      'Bearer for staging NFL current-season refresh'
    );
  end if;

  command := format($command$
    select net.http_post(
      url := %L,
      headers := jsonb_build_object(
        'Content-Type','application/json',
        'Authorization','Bearer ' || (
          select decrypted_secret from vault.decrypted_secrets
          where name='nfl_data_refresh_cron_bearer'
        ),
        'x-vercel-protection-bypass',coalesce((
          select decrypted_secret from vault.decrypted_secrets
          where name='nba_personnel_vercel_bypass'
        ),'')
      ),
      body := '{}'::jsonb,
      timeout_milliseconds := 25000
    );
  $command$, target);

  select jobid into prior_job from cron.job
   where jobname = 'smartbets-nfl-data-refresh-30m-staging';
  if prior_job is not null then
    perform cron.unschedule(prior_job);
  end if;
  installed_job := cron.schedule(
    'smartbets-nfl-data-refresh-30m-staging', '*/30 * * * *', command
  );
  return jsonb_build_object(
    'installed', true, 'jobId', installed_job,
    'jobName', 'smartbets-nfl-data-refresh-30m-staging',
    'schedule', '*/30 * * * *', 'target', target,
    'secretStorage', 'supabase_vault', 'secretExposed', false
  );
end
$$;

create or replace function public.nfl_data_refresh_cron_status()
returns jsonb
language sql
security definer
set search_path = pg_catalog, public, cron
as $$
  select jsonb_build_object(
    'job', coalesce((
      select to_jsonb(j) from (
        select jobid,jobname,schedule,active
        from cron.job
        where jobname='smartbets-nfl-data-refresh-30m-staging'
      ) j
    ), 'null'::jsonb),
    'latestRun', coalesce((
      select to_jsonb(r) from (
        select d.status,d.start_time,d.end_time,d.return_message
        from cron.job_run_details d
        join cron.job j on j.jobid=d.jobid
        where j.jobname='smartbets-nfl-data-refresh-30m-staging'
        order by d.start_time desc limit 1
      ) r
    ), 'null'::jsonb)
  )
$$;

revoke all on function public.install_nfl_data_refresh_cron(text,text) from public;
revoke all on function public.nfl_data_refresh_cron_status() from public;
do $$
begin
  if exists (select 1 from pg_roles where rolname='smartbets_staging_app') then
    execute 'grant execute on function public.install_nfl_data_refresh_cron(text,text) to smartbets_staging_app';
    execute 'grant execute on function public.nfl_data_refresh_cron_status() to smartbets_staging_app';
  end if;
end
$$;

commit;
