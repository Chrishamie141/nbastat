-- Give only the dedicated direct-Postgres staging role the operations required
-- by the worker. Browser/API roles remain revoked and have no policies.
do $$
begin
  if exists (select 1 from pg_roles where rolname='smartbets_staging_app') then
    grant select,insert on public.nfl_team_game_history to smartbets_staging_app;
    grant select,insert,update on public.nfl_data_pipeline_runs to smartbets_staging_app;
    grant select,update on public.nfl_data_pipeline_state to smartbets_staging_app;
    grant select,insert on public.nfl_forecast_snapshots to smartbets_staging_app;
  end if;
end
$$;

drop policy if exists nfl_team_history_staging_read on public.nfl_team_game_history;
create policy nfl_team_history_staging_read on public.nfl_team_game_history
  for select to public using (current_user = 'smartbets_staging_app');
drop policy if exists nfl_team_history_staging_insert on public.nfl_team_game_history;
create policy nfl_team_history_staging_insert on public.nfl_team_game_history
  for insert to public with check (current_user = 'smartbets_staging_app');

drop policy if exists nfl_pipeline_runs_staging_access on public.nfl_data_pipeline_runs;
create policy nfl_pipeline_runs_staging_access on public.nfl_data_pipeline_runs
  for all to public using (current_user = 'smartbets_staging_app')
  with check (current_user = 'smartbets_staging_app');
drop policy if exists nfl_pipeline_state_staging_access on public.nfl_data_pipeline_state;
create policy nfl_pipeline_state_staging_access on public.nfl_data_pipeline_state
  for all to public using (current_user = 'smartbets_staging_app')
  with check (current_user = 'smartbets_staging_app');

drop policy if exists nfl_forecasts_staging_read on public.nfl_forecast_snapshots;
create policy nfl_forecasts_staging_read on public.nfl_forecast_snapshots
  for select to public using (current_user = 'smartbets_staging_app');
drop policy if exists nfl_forecasts_staging_insert on public.nfl_forecast_snapshots;
create policy nfl_forecasts_staging_insert on public.nfl_forecast_snapshots
  for insert to public with check (current_user = 'smartbets_staging_app');
