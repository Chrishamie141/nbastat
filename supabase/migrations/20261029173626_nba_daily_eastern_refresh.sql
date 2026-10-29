-- Add immutable, versioned upcoming forecasts and one DST-safe production
-- scheduler. Existing official snapshots and settlements are not rewritten.
begin;

alter table public.nba_prediction_snapshots
  add column if not exists snapshot_kind text not null default 'OFFICIAL';

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid='public.nba_prediction_snapshots'::regclass
      and conname='nba_prediction_snapshots_kind_check'
  ) then
    alter table public.nba_prediction_snapshots
      add constraint nba_prediction_snapshots_kind_check
      check (snapshot_kind in ('OFFICIAL','LATEST_FORECAST'));
  end if;
end
$$;

create index if not exists idx_nba_latest_forecasts
  on public.nba_prediction_snapshots
  (season_code,game_id,model_version,generated_at desc)
  where snapshot_kind='LATEST_FORECAST';

create or replace function public.install_nba_data_refresh_cron(
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
  if deployment_environment not in ('staging','production') then
    raise exception 'invalid NBA refresh environment';
  end if;
  expected_role := case deployment_environment
    when 'staging' then 'smartbets_staging_app'
    else 'smartbets_production_app'
  end;
  if session_user <> expected_role then
    raise exception 'NBA refresh environment does not match database role';
  end if;
  if base_url !~ '^https://[a-zA-Z0-9-]+\.vercel\.app/?$' then
    raise exception 'invalid Vercel origin';
  end if;
  if length(bearer_secret) < 32 then
    raise exception 'invalid NBA refresh bearer';
  end if;

  job_name := case deployment_environment
    when 'production' then 'smartbets-nba-data-refresh-daily-production'
    else 'smartbets-nba-data-refresh-30m-staging'
  end;
  schedule := case deployment_environment
    when 'production' then '0 7,8 * * *'
    else '*/30 * * * *'
  end;
  secret_name := 'nba_data_refresh_cron_bearer_' || deployment_environment;
  target := rtrim(base_url,'/') || '/api/cron/nba-data-refresh';

  select id into secret_id from vault.secrets where name=secret_name;
  if secret_id is null then
    perform vault.create_secret(
      bearer_secret, secret_name,
      'Bearer for ' || deployment_environment || ' NBA current-season refresh'
    );
  else
    perform vault.update_secret(
      secret_id, bearer_secret, secret_name,
      'Bearer for ' || deployment_environment || ' NBA current-season refresh'
    );
  end if;

  if deployment_environment='production' then
    command := format($command$
      select case when
        (now() at time zone 'America/New_York')::time >= time '03:00'
        and (now() at time zone 'America/New_York')::time < time '03:10'
        and not exists (
          select 1 from public.nba_automation_runs
          where trigger_source='supabase_cron'
            and status in ('RUNNING','SUCCEEDED')
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
          'x-smartbets-trigger','supabase_cron'
        ),
        body := jsonb_build_object('environment','production'),
        timeout_milliseconds := 150000
      ) end;
    $command$,target,secret_name);
  else
    command := format($command$
      select net.http_post(
        url := %L,
        headers := jsonb_build_object(
          'Content-Type','application/json',
          'Authorization','Bearer ' || (
            select decrypted_secret from vault.decrypted_secrets where name=%L
          ),
          'x-smartbets-trigger','supabase_cron'
        ),
        body := jsonb_build_object('environment','staging'),
        timeout_milliseconds := 150000
      );
    $command$,target,secret_name);
  end if;

  for prior_job in
    select jobid from cron.job
    where jobname in (
      job_name,
      'smartbets-nba-data-refresh-30m-production',
      'smartbets-nba-active-season'
    )
  loop
    perform cron.unschedule(prior_job);
  end loop;
  installed_job := cron.schedule(job_name,schedule,command);

  return jsonb_build_object(
    'installed',true,'jobId',installed_job,'jobName',job_name,
    'environment',deployment_environment,'schedule',schedule,
    'timeZone',case when deployment_environment='production'
      then 'America/New_York' else 'UTC' end,
    'localTime',case when deployment_environment='production'
      then '03:00' else null end,
    'utcCandidates',case when deployment_environment='production'
      then jsonb_build_array('07:00','08:00') else null end,
    'target',target,'secretStorage','supabase_vault','secretExposed',false
  );
end
$$;

create or replace function public.nba_data_refresh_cron_status(
  deployment_environment text
) returns jsonb
language plpgsql
security definer
set search_path = pg_catalog, public, cron
as $$
declare
  job_name text;
  expected_role text;
begin
  if deployment_environment not in ('staging','production') then
    raise exception 'invalid NBA refresh environment';
  end if;
  expected_role := case deployment_environment
    when 'staging' then 'smartbets_staging_app'
    else 'smartbets_production_app'
  end;
  if session_user <> expected_role then
    raise exception 'NBA refresh environment does not match database role';
  end if;
  job_name := case deployment_environment
    when 'production' then 'smartbets-nba-data-refresh-daily-production'
    else 'smartbets-nba-data-refresh-30m-staging'
  end;
  return jsonb_build_object(
    'job',coalesce((
      select to_jsonb(j) from (
        select jobid,jobname,schedule,active from cron.job where jobname=job_name
      ) j
    ),'null'::jsonb),
    'latestRun',coalesce((
      select to_jsonb(r) from (
        select d.status,d.start_time,d.end_time,d.return_message
        from cron.job_run_details d join cron.job j on j.jobid=d.jobid
        where j.jobname=job_name order by d.start_time desc limit 1
      ) r
    ),'null'::jsonb),
    'policy',jsonb_build_object(
      'timeZone',case when deployment_environment='production'
        then 'America/New_York' else 'UTC' end,
      'localTime',case when deployment_environment='production'
        then '03:00' else null end,
      'utcCandidates',case when deployment_environment='production'
        then jsonb_build_array('07:00','08:00') else null end
    )
  );
end
$$;

revoke all on function public.install_nba_data_refresh_cron(text,text,text)
  from public,anon,authenticated,service_role;
revoke all on function public.nba_data_refresh_cron_status(text)
  from public,anon,authenticated,service_role;

do $$
declare
  role_name text;
begin
  foreach role_name in array array['smartbets_staging_app','smartbets_production_app']
  loop
    if exists(select 1 from pg_roles where rolname=role_name) then
      execute format('grant usage on schema public to %I',role_name);
      execute format('grant select,insert,update on public.nba_games to %I',role_name);
      execute format('grant select,insert on public.nba_prediction_snapshots to %I',role_name);
      execute format('grant select,insert,update on public.nba_settlements to %I',role_name);
      execute format('grant select,insert,update on public.nba_automation_runs to %I',role_name);
      execute format('grant select on public.nba_operational_settings to %I',role_name);
      execute format('grant select on public.nba_model_registry to %I',role_name);
      execute format('grant select on public.nba_market_observations to %I',role_name);
      execute format('grant select on public.nba_replay_runs to %I',role_name);
      execute format('grant select,insert on public.nba_player_availability_snapshots to %I',role_name);
      execute format('grant select,insert,update on public.nba_data_source_status to %I',role_name);
      execute format(
        'grant execute on function public.install_nba_data_refresh_cron(text,text,text) to %I',
        role_name
      );
      execute format(
        'grant execute on function public.nba_data_refresh_cron_status(text) to %I',
        role_name
      );
    end if;
  end loop;
end
$$;

drop policy if exists nba_pipeline_games on public.nba_games;
create policy nba_pipeline_games on public.nba_games for all to public
  using(current_user in ('smartbets_staging_app','smartbets_production_app'))
  with check(current_user in ('smartbets_staging_app','smartbets_production_app'));

drop policy if exists nba_pipeline_predictions_read on public.nba_prediction_snapshots;
create policy nba_pipeline_predictions_read on public.nba_prediction_snapshots
  for select to public
  using(current_user in ('smartbets_staging_app','smartbets_production_app'));
drop policy if exists nba_pipeline_predictions_insert on public.nba_prediction_snapshots;
create policy nba_pipeline_predictions_insert on public.nba_prediction_snapshots
  for insert to public
  with check(
    current_user in ('smartbets_staging_app','smartbets_production_app')
    and user_id=0
  );

drop policy if exists nba_pipeline_settlements on public.nba_settlements;
create policy nba_pipeline_settlements on public.nba_settlements for all to public
  using(current_user in ('smartbets_staging_app','smartbets_production_app'))
  with check(current_user in ('smartbets_staging_app','smartbets_production_app'));

drop policy if exists nba_pipeline_runs on public.nba_automation_runs;
create policy nba_pipeline_runs on public.nba_automation_runs for all to public
  using(current_user in ('smartbets_staging_app','smartbets_production_app'))
  with check(current_user in ('smartbets_staging_app','smartbets_production_app'));

drop policy if exists nba_pipeline_settings_read on public.nba_operational_settings;
create policy nba_pipeline_settings_read on public.nba_operational_settings
  for select to public
  using(current_user in ('smartbets_staging_app','smartbets_production_app'));

drop policy if exists nba_pipeline_registry_read on public.nba_model_registry;
create policy nba_pipeline_registry_read on public.nba_model_registry
  for select to public
  using(current_user in ('smartbets_staging_app','smartbets_production_app'));

drop policy if exists nba_pipeline_markets_read on public.nba_market_observations;
create policy nba_pipeline_markets_read on public.nba_market_observations
  for select to public
  using(current_user in ('smartbets_staging_app','smartbets_production_app'));

drop policy if exists nba_pipeline_replay_read on public.nba_replay_runs;
create policy nba_pipeline_replay_read on public.nba_replay_runs
  for select to public
  using(current_user in ('smartbets_staging_app','smartbets_production_app'));

drop policy if exists nba_pipeline_personnel_read on public.nba_player_availability_snapshots;
create policy nba_pipeline_personnel_read on public.nba_player_availability_snapshots
  for select to public
  using(current_user in ('smartbets_staging_app','smartbets_production_app'));
drop policy if exists nba_pipeline_personnel_insert on public.nba_player_availability_snapshots;
create policy nba_pipeline_personnel_insert on public.nba_player_availability_snapshots
  for insert to public
  with check(current_user in ('smartbets_staging_app','smartbets_production_app'));

drop policy if exists nba_pipeline_source_status on public.nba_data_source_status;
create policy nba_pipeline_source_status on public.nba_data_source_status for all to public
  using(current_user in ('smartbets_staging_app','smartbets_production_app'))
  with check(current_user in ('smartbets_staging_app','smartbets_production_app'));

commit;
