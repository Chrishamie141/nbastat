-- Additive, server-only current-season NFL history and forecast lifecycle.
-- Existing evaluated predictions, grades, and NFL V2 model artifacts are not changed.
begin;

create table if not exists public.nfl_team_game_history (
  history_key text primary key,
  season integer not null,
  season_type text not null check (season_type in ('regular','postseason')),
  week integer not null check (week > 0),
  game_id text not null,
  team text not null,
  opponent text not null,
  home_away text not null check (home_away in ('home','away')),
  points_for integer not null check (points_for >= 0),
  points_against integer not null check (points_against >= 0),
  kickoff_time text not null,
  completed_at text not null,
  source_completed_at text,
  first_observed_final_at text not null,
  last_verified_at text not null,
  ingested_at text not null,
  data_as_of text not null,
  provider text not null,
  provider_status text not null,
  source_hash text not null,
  provenance_json text not null,
  record_role text not null default 'completed_game_history',
  is_pregame integer not null default 0 check (is_pregame = 0),
  unique (season, season_type, game_id, team)
);

create index if not exists idx_nfl_team_history_cutoff
  on public.nfl_team_game_history(season, season_type, completed_at, team);

create table if not exists public.nfl_data_pipeline_runs (
  run_id text primary key,
  trigger_source text not null,
  season integer not null,
  started_at text not null,
  completed_at text,
  status text not null,
  safe_error_code text,
  weeks_checked integer not null default 0,
  games_observed integer not null default 0,
  history_rows_inserted integer not null default 0,
  duplicate_rows integer not null default 0,
  conflict_rows integer not null default 0,
  forecasts_created integer not null default 0,
  forecasts_unchanged integer not null default 0,
  officials_frozen integer not null default 0,
  latest_history_cutoff text,
  details_json text not null default '{}'
);

create table if not exists public.nfl_data_pipeline_state (
  singleton integer primary key check (singleton = 1),
  enabled integer not null default 1,
  last_started_at text,
  last_success_at text,
  last_status text not null default 'BOOTSTRAP',
  last_error_code text,
  consecutive_failures integer not null default 0,
  last_season integer,
  last_week integer,
  latest_game_id text,
  latest_history_cutoff text,
  last_forecast_refresh_at text,
  updated_at text not null
);

create table if not exists public.nfl_forecast_snapshots (
  forecast_id text primary key,
  run_id text references public.nfl_data_pipeline_runs(run_id),
  game_id text not null,
  season integer not null,
  season_type text not null check (season_type = 'regular'),
  display_week integer not null,
  kickoff_time text not null,
  generated_at text not null,
  model_version text not null,
  history_cutoff text not null,
  history_hash text not null,
  history_row_count integer not null check (history_row_count > 0),
  prediction_hash text not null,
  prediction_json text not null,
  official_prediction_id bigint,
  unique (game_id, model_version, history_hash, prediction_hash)
);

create index if not exists idx_nfl_forecast_latest
  on public.nfl_forecast_snapshots(game_id, generated_at desc);

insert into public.nfl_data_pipeline_state(singleton, enabled, last_status, updated_at)
values (1, 1, 'BOOTSTRAP', now()::text)
on conflict (singleton) do nothing;

create or replace function public.reject_nfl_history_change()
returns trigger language plpgsql set search_path = pg_catalog as $$
begin
  raise exception 'immutable NFL current-season evidence';
end
$$;

drop trigger if exists nfl_team_history_immutable_update on public.nfl_team_game_history;
create trigger nfl_team_history_immutable_update before update on public.nfl_team_game_history
for each row execute function public.reject_nfl_history_change();
drop trigger if exists nfl_team_history_immutable_delete on public.nfl_team_game_history;
create trigger nfl_team_history_immutable_delete before delete on public.nfl_team_game_history
for each row execute function public.reject_nfl_history_change();
drop trigger if exists nfl_forecast_immutable_update on public.nfl_forecast_snapshots;
create trigger nfl_forecast_immutable_update before update on public.nfl_forecast_snapshots
for each row execute function public.reject_nfl_history_change();
drop trigger if exists nfl_forecast_immutable_delete on public.nfl_forecast_snapshots;
create trigger nfl_forecast_immutable_delete before delete on public.nfl_forecast_snapshots
for each row execute function public.reject_nfl_history_change();

alter table public.nfl_team_game_history enable row level security;
alter table public.nfl_data_pipeline_runs enable row level security;
alter table public.nfl_data_pipeline_state enable row level security;
alter table public.nfl_forecast_snapshots enable row level security;

revoke all on table public.nfl_team_game_history from anon, authenticated, service_role;
revoke all on table public.nfl_data_pipeline_runs from anon, authenticated, service_role;
revoke all on table public.nfl_data_pipeline_state from anon, authenticated, service_role;
revoke all on table public.nfl_forecast_snapshots from anon, authenticated, service_role;

commit;
