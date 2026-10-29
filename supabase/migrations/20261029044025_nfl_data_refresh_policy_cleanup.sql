-- Remove superseded staging-only policies after the environment-neutral
-- equivalents have been installed. This changes no data or table privileges.
begin;

drop policy if exists nfl_team_history_staging_read
  on public.nfl_team_game_history;
drop policy if exists nfl_team_history_staging_insert
  on public.nfl_team_game_history;

drop policy if exists nfl_pipeline_runs_staging_access
  on public.nfl_data_pipeline_runs;
drop policy if exists nfl_pipeline_runs_staging_read
  on public.nfl_data_pipeline_runs;
drop policy if exists nfl_pipeline_runs_staging_insert
  on public.nfl_data_pipeline_runs;
drop policy if exists nfl_pipeline_runs_staging_update
  on public.nfl_data_pipeline_runs;

drop policy if exists nfl_pipeline_state_staging_access
  on public.nfl_data_pipeline_state;
drop policy if exists nfl_pipeline_state_staging_read
  on public.nfl_data_pipeline_state;
drop policy if exists nfl_pipeline_state_staging_update
  on public.nfl_data_pipeline_state;

drop policy if exists nfl_forecasts_staging_read
  on public.nfl_forecast_snapshots;
drop policy if exists nfl_forecasts_staging_insert
  on public.nfl_forecast_snapshots;

commit;
