begin;

revoke all on function public.install_nfl_data_refresh_cron(text,text)
  from public, anon, authenticated, service_role;
revoke all on function public.nfl_data_refresh_cron_status()
  from public, anon, authenticated, service_role;

do $$
begin
  if exists (select 1 from pg_roles where rolname='smartbets_staging_app') then
    revoke all on public.nfl_team_game_history from smartbets_staging_app;
    revoke all on public.nfl_data_pipeline_runs from smartbets_staging_app;
    revoke all on public.nfl_data_pipeline_state from smartbets_staging_app;
    revoke all on public.nfl_forecast_snapshots from smartbets_staging_app;
    grant select,insert on public.nfl_team_game_history to smartbets_staging_app;
    grant select,insert,update on public.nfl_data_pipeline_runs to smartbets_staging_app;
    grant select,update on public.nfl_data_pipeline_state to smartbets_staging_app;
    grant select,insert on public.nfl_forecast_snapshots to smartbets_staging_app;
    execute 'grant execute on function public.install_nfl_data_refresh_cron(text,text) to smartbets_staging_app';
    execute 'grant execute on function public.nfl_data_refresh_cron_status() to smartbets_staging_app';
  end if;
end
$$;

drop policy if exists nfl_pipeline_runs_staging_access on public.nfl_data_pipeline_runs;
drop policy if exists nfl_pipeline_runs_staging_read on public.nfl_data_pipeline_runs;
create policy nfl_pipeline_runs_staging_read on public.nfl_data_pipeline_runs
  for select to public using (current_user = 'smartbets_staging_app');
drop policy if exists nfl_pipeline_runs_staging_insert on public.nfl_data_pipeline_runs;
create policy nfl_pipeline_runs_staging_insert on public.nfl_data_pipeline_runs
  for insert to public with check (current_user = 'smartbets_staging_app');
drop policy if exists nfl_pipeline_runs_staging_update on public.nfl_data_pipeline_runs;
create policy nfl_pipeline_runs_staging_update on public.nfl_data_pipeline_runs
  for update to public using (current_user = 'smartbets_staging_app')
  with check (current_user = 'smartbets_staging_app');

drop policy if exists nfl_pipeline_state_staging_access on public.nfl_data_pipeline_state;
drop policy if exists nfl_pipeline_state_staging_read on public.nfl_data_pipeline_state;
create policy nfl_pipeline_state_staging_read on public.nfl_data_pipeline_state
  for select to public using (current_user = 'smartbets_staging_app');
drop policy if exists nfl_pipeline_state_staging_update on public.nfl_data_pipeline_state;
create policy nfl_pipeline_state_staging_update on public.nfl_data_pipeline_state
  for update to public using (current_user = 'smartbets_staging_app')
  with check (current_user = 'smartbets_staging_app');

commit;
