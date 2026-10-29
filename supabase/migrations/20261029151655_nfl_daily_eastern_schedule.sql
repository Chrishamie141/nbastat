-- Install the production NFL refresh as one DST-safe daily job. pg_cron runs
-- in UTC, so the production schedule evaluates both possible UTC hours and
-- the command only dispatches at 03:00 America/New_York. The staging cadence
-- remains unchanged.
begin;

create or replace function public.install_nfl_data_refresh_cron(
  base_url text,
  bearer_secret text,
  deployment_environment text
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
  job_name text;
  schedule text;
  secret_name text;
  expected_role text;
begin
  if deployment_environment not in ('staging', 'production') then
    raise exception 'invalid NFL refresh environment';
  end if;
  expected_role := case deployment_environment
    when 'staging' then 'smartbets_staging_app'
    else 'smartbets_production_app'
  end;
  if session_user <> expected_role then
    raise exception 'NFL refresh environment does not match database role';
  end if;
  if base_url !~ '^https://[a-zA-Z0-9-]+\.vercel\.app/?$' then
    raise exception 'invalid Vercel origin';
  end if;
  if length(bearer_secret) < 32 then
    raise exception 'invalid refresh bearer';
  end if;

  job_name := case deployment_environment
    when 'production' then 'smartbets-nfl-data-refresh-daily-production'
    else 'smartbets-nfl-data-refresh-30m-staging'
  end;
  schedule := case deployment_environment
    when 'production' then '0 7,8 * * *'
    else '*/30 * * * *'
  end;
  secret_name := 'nfl_data_refresh_cron_bearer_' || deployment_environment;
  target := rtrim(base_url, '/') || '/api/cron/nfl-data-refresh';

  select id into secret_id from vault.secrets where name = secret_name;
  if secret_id is null then
    perform vault.create_secret(
      bearer_secret,
      secret_name,
      'Bearer for ' || deployment_environment || ' NFL current-season refresh'
    );
  else
    perform vault.update_secret(
      secret_id,
      bearer_secret,
      secret_name,
      'Bearer for ' || deployment_environment || ' NFL current-season refresh'
    );
  end if;

  if deployment_environment = 'production' then
    command := format($command$
      select case when
        (now() at time zone 'America/New_York')::time >= time '03:00'
        and (now() at time zone 'America/New_York')::time < time '03:10'
        and not exists (
          select 1
          from public.nfl_data_pipeline_runs
          where trigger_source = 'supabase_cron'
            and status in ('RUNNING', 'SUCCEEDED')
            and (started_at::timestamptz at time zone 'America/New_York')::date
                = (now() at time zone 'America/New_York')::date
        )
      then net.http_post(
        url := %L,
        headers := jsonb_build_object(
          'Content-Type','application/json',
          'Authorization','Bearer ' || (
            select decrypted_secret from vault.decrypted_secrets where name=%L
          ),
          'x-vercel-protection-bypass',coalesce((
            select decrypted_secret from vault.decrypted_secrets
            where name='nba_personnel_vercel_bypass'
          ),''),
          'x-smartbets-trigger','supabase_cron'
        ),
        body := jsonb_build_object('environment', 'production'),
        timeout_milliseconds := 25000
      ) end;
    $command$, target, secret_name);
  else
    command := format($command$
      select net.http_post(
        url := %L,
        headers := jsonb_build_object(
          'Content-Type','application/json',
          'Authorization','Bearer ' || (
            select decrypted_secret from vault.decrypted_secrets where name=%L
          ),
          'x-vercel-protection-bypass',coalesce((
            select decrypted_secret from vault.decrypted_secrets
            where name='nba_personnel_vercel_bypass'
          ),''),
          'x-smartbets-trigger','supabase_cron'
        ),
        body := jsonb_build_object('environment', 'staging'),
        timeout_milliseconds := 25000
      );
    $command$, target, secret_name);
  end if;

  -- Reinstallation is idempotent and removes the obsolete production name.
  for prior_job in
    select jobid from cron.job
    where jobname in (
      job_name,
      case when deployment_environment = 'production'
        then 'smartbets-nfl-data-refresh-30m-production'
        else '__unused__'
      end
    )
  loop
    perform cron.unschedule(prior_job);
  end loop;
  installed_job := cron.schedule(job_name, schedule, command);

  return jsonb_build_object(
    'installed', true,
    'jobId', installed_job,
    'jobName', job_name,
    'environment', deployment_environment,
    'schedule', schedule,
    'timeZone', case when deployment_environment = 'production'
      then 'America/New_York' else 'UTC' end,
    'localTime', case when deployment_environment = 'production'
      then '03:00' else null end,
    'utcCandidates', case when deployment_environment = 'production'
      then jsonb_build_array('07:00', '08:00') else null end,
    'target', target,
    'secretStorage', 'supabase_vault',
    'secretExposed', false
  );
end
$$;

create or replace function public.nfl_data_refresh_cron_status(
  deployment_environment text
) returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public, cron
as $$
declare
  job_name text;
  expected_role text;
  result jsonb;
begin
  if deployment_environment not in ('staging', 'production') then
    raise exception 'invalid NFL refresh environment';
  end if;
  expected_role := case deployment_environment
    when 'staging' then 'smartbets_staging_app'
    else 'smartbets_production_app'
  end;
  if session_user <> expected_role then
    raise exception 'NFL refresh environment does not match database role';
  end if;
  job_name := case deployment_environment
    when 'production' then 'smartbets-nfl-data-refresh-daily-production'
    else 'smartbets-nfl-data-refresh-30m-staging'
  end;

  select jsonb_build_object(
    'job', coalesce((
      select to_jsonb(j) from (
        select jobid,jobname,schedule,active
        from cron.job where jobname=job_name
      ) j
    ), 'null'::jsonb),
    'latestRun', coalesce((
      select to_jsonb(r) from (
        select d.status,d.start_time,d.end_time,d.return_message
        from cron.job_run_details d
        join cron.job j on j.jobid=d.jobid
        where j.jobname=job_name
        order by d.start_time desc limit 1
      ) r
    ), 'null'::jsonb),
    'policy', jsonb_build_object(
      'timeZone', case when deployment_environment = 'production'
        then 'America/New_York' else 'UTC' end,
      'localTime', case when deployment_environment = 'production'
        then '03:00' else null end,
      'utcCandidates', case when deployment_environment = 'production'
        then jsonb_build_array('07:00', '08:00') else null end
    )
  ) into result;
  return result;
end
$$;

revoke all on function public.install_nfl_data_refresh_cron(text,text,text)
  from public, anon, authenticated, service_role;
revoke all on function public.nfl_data_refresh_cron_status(text)
  from public, anon, authenticated, service_role;

do $$
declare
  role_name text;
begin
  foreach role_name in array array['smartbets_staging_app','smartbets_production_app']
  loop
    if exists (select 1 from pg_roles where rolname=role_name) then
      execute format(
        'grant execute on function public.install_nfl_data_refresh_cron(text,text,text) to %I',
        role_name
      );
      execute format(
        'grant execute on function public.nfl_data_refresh_cron_status(text) to %I',
        role_name
      );
    end if;
  end loop;
end
$$;

commit;
