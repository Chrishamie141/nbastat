-- Environment-neutral, least-privilege Cron boundary. This reconciles the
-- staging implementation and supplies a production path without installing
-- or activating a production job.
begin;

create index if not exists idx_nfl_forecast_snapshots_run_id
  on public.nfl_forecast_snapshots(run_id);

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

  job_name := 'smartbets-nfl-data-refresh-30m-' || deployment_environment;
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

  command := format($command$
    select net.http_post(
      url := %L,
      headers := jsonb_build_object(
        'Content-Type','application/json',
        'Authorization','Bearer ' || (
          select decrypted_secret from vault.decrypted_secrets
          where name=%L
        ),
        'x-vercel-protection-bypass',coalesce((
          select decrypted_secret from vault.decrypted_secrets
          where name='nba_personnel_vercel_bypass'
        ),'')
      ),
      body := jsonb_build_object('environment', %L),
      timeout_milliseconds := 25000
    );
  $command$, target, secret_name, deployment_environment);

  select jobid into prior_job from cron.job where jobname = job_name;
  if prior_job is not null then
    perform cron.unschedule(prior_job);
  end if;
  installed_job := cron.schedule(job_name, '*/30 * * * *', command);

  return jsonb_build_object(
    'installed', true,
    'jobId', installed_job,
    'jobName', job_name,
    'environment', deployment_environment,
    'schedule', '*/30 * * * *',
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
  job_name := 'smartbets-nfl-data-refresh-30m-' || deployment_environment;

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
    ), 'null'::jsonb)
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
      execute format('revoke all on public.nfl_team_game_history from %I', role_name);
      execute format('revoke all on public.nfl_data_pipeline_runs from %I', role_name);
      execute format('revoke all on public.nfl_data_pipeline_state from %I', role_name);
      execute format('revoke all on public.nfl_forecast_snapshots from %I', role_name);
      execute format('grant select,insert on public.nfl_team_game_history to %I', role_name);
      execute format('grant select,insert,update on public.nfl_data_pipeline_runs to %I', role_name);
      execute format('grant select,update on public.nfl_data_pipeline_state to %I', role_name);
      execute format('grant select,insert on public.nfl_forecast_snapshots to %I', role_name);
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

drop policy if exists nfl_team_history_environment_read on public.nfl_team_game_history;
create policy nfl_team_history_environment_read on public.nfl_team_game_history
  for select to public using (
    current_user in ('smartbets_staging_app','smartbets_production_app')
  );
drop policy if exists nfl_team_history_environment_insert on public.nfl_team_game_history;
create policy nfl_team_history_environment_insert on public.nfl_team_game_history
  for insert to public with check (
    current_user in ('smartbets_staging_app','smartbets_production_app')
  );

drop policy if exists nfl_pipeline_runs_environment_read on public.nfl_data_pipeline_runs;
create policy nfl_pipeline_runs_environment_read on public.nfl_data_pipeline_runs
  for select to public using (
    current_user in ('smartbets_staging_app','smartbets_production_app')
  );
drop policy if exists nfl_pipeline_runs_environment_insert on public.nfl_data_pipeline_runs;
create policy nfl_pipeline_runs_environment_insert on public.nfl_data_pipeline_runs
  for insert to public with check (
    current_user in ('smartbets_staging_app','smartbets_production_app')
  );
drop policy if exists nfl_pipeline_runs_environment_update on public.nfl_data_pipeline_runs;
create policy nfl_pipeline_runs_environment_update on public.nfl_data_pipeline_runs
  for update to public using (
    current_user in ('smartbets_staging_app','smartbets_production_app')
  ) with check (
    current_user in ('smartbets_staging_app','smartbets_production_app')
  );

drop policy if exists nfl_pipeline_state_environment_read on public.nfl_data_pipeline_state;
create policy nfl_pipeline_state_environment_read on public.nfl_data_pipeline_state
  for select to public using (
    current_user in ('smartbets_staging_app','smartbets_production_app')
  );
drop policy if exists nfl_pipeline_state_environment_update on public.nfl_data_pipeline_state;
create policy nfl_pipeline_state_environment_update on public.nfl_data_pipeline_state
  for update to public using (
    current_user in ('smartbets_staging_app','smartbets_production_app')
  ) with check (
    current_user in ('smartbets_staging_app','smartbets_production_app')
  );

drop policy if exists nfl_forecasts_environment_read on public.nfl_forecast_snapshots;
create policy nfl_forecasts_environment_read on public.nfl_forecast_snapshots
  for select to public using (
    current_user in ('smartbets_staging_app','smartbets_production_app')
  );
drop policy if exists nfl_forecasts_environment_insert on public.nfl_forecast_snapshots;
create policy nfl_forecasts_environment_insert on public.nfl_forecast_snapshots
  for insert to public with check (
    current_user in ('smartbets_staging_app','smartbets_production_app')
  );

commit;
