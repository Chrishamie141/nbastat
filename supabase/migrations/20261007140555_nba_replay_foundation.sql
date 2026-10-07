-- NBA-only replay and production evidence. Server-side access only.
create table if not exists public.nba_games (
  game_id text primary key,
  season_code text not null check (season_code ~ '^20[0-9]{2}-[0-9]{2}$'),
  kickoff_time text not null,
  home_team text not null,
  away_team text not null,
  status text not null check (status in ('SCHEDULED','LIVE','FINAL','POSTPONED','CANCELED')),
  home_score integer,
  away_score integer,
  provider text not null,
  provider_event_id text,
  provider_timestamp text,
  retrieved_at text not null,
  source_hash text not null
);

create index if not exists idx_nba_games_season_kickoff
  on public.nba_games(season_code, kickoff_time, status);

create table if not exists public.nba_prediction_snapshots (
  snapshot_id text primary key,
  user_id bigint not null default 0,
  game_id text not null,
  season_code text not null,
  kickoff_time text not null,
  market text not null,
  selection text not null,
  line double precision,
  player_id text,
  player_name text,
  model_probability double precision check (model_probability is null or (model_probability >= 0 and model_probability <= 1)),
  projection double precision,
  edge double precision,
  market_odds integer,
  model_version text not null check (model_version like 'nba-%'),
  data_version text,
  generated_at text not null,
  feature_cutoff text not null,
  source_provider text,
  payload_json text not null,
  snapshot_hash text not null,
  created_at text not null
);

create index if not exists idx_nba_predictions_game
  on public.nba_prediction_snapshots(season_code, game_id, generated_at);

create table if not exists public.nba_market_observations (
  observation_id text primary key,
  game_id text not null,
  season_code text not null,
  provider text not null,
  sportsbook text not null,
  market text not null,
  selection text not null,
  line double precision,
  odds integer not null,
  observed_at text not null,
  kickoff_time text not null,
  provider_event_id text,
  payload_json text not null,
  source_hash text not null,
  created_at text not null,
  unique nulls not distinct (game_id, provider, sportsbook, market, selection, line, odds, observed_at)
);

create index if not exists idx_nba_markets_game_time
  on public.nba_market_observations(game_id, observed_at);

create table if not exists public.nba_settlements (
  prediction_snapshot_id text primary key references public.nba_prediction_snapshots(snapshot_id),
  game_id text not null,
  status text not null check (status in ('WIN','LOSS','PUSH','VOID','UNRESOLVED')),
  actual_value text,
  reason text,
  final_source_hash text not null,
  settled_at text not null
);

create index if not exists idx_nba_settlements_game
  on public.nba_settlements(game_id, status);

create table if not exists public.nba_replay_runs (
  run_id text primary key,
  season_code text not null,
  model_version text not null check (model_version like 'nba-%'),
  started_at text not null,
  completed_at text,
  status text not null check (status in ('RUNNING','COMPLETE','FAILED')),
  config_json text not null,
  metrics_json text,
  source_hash text not null
);

create or replace function public.reject_nba_immutable_evidence_change()
returns trigger language plpgsql set search_path = '' as $$
begin
  raise exception 'immutable NBA evidence';
end;
$$;

drop trigger if exists nba_prediction_snapshots_immutable_update on public.nba_prediction_snapshots;
create trigger nba_prediction_snapshots_immutable_update
before update on public.nba_prediction_snapshots for each row
execute function public.reject_nba_immutable_evidence_change();

drop trigger if exists nba_prediction_snapshots_immutable_delete on public.nba_prediction_snapshots;
create trigger nba_prediction_snapshots_immutable_delete
before delete on public.nba_prediction_snapshots for each row
execute function public.reject_nba_immutable_evidence_change();

drop trigger if exists nba_market_observations_immutable_update on public.nba_market_observations;
create trigger nba_market_observations_immutable_update
before update on public.nba_market_observations for each row
execute function public.reject_nba_immutable_evidence_change();

drop trigger if exists nba_market_observations_immutable_delete on public.nba_market_observations;
create trigger nba_market_observations_immutable_delete
before delete on public.nba_market_observations for each row
execute function public.reject_nba_immutable_evidence_change();

create or replace function public.validate_nba_pregame_evidence()
returns trigger language plpgsql set search_path = '' as $$
begin
  if new.generated_at::timestamptz >= new.kickoff_time::timestamptz
     or new.feature_cutoff::timestamptz > new.generated_at::timestamptz then
    raise exception 'NBA prediction evidence must be frozen before kickoff';
  end if;
  return new;
end;
$$;

drop trigger if exists nba_prediction_snapshots_pregame on public.nba_prediction_snapshots;
create trigger nba_prediction_snapshots_pregame
before insert on public.nba_prediction_snapshots for each row
execute function public.validate_nba_pregame_evidence();

create or replace function public.validate_nba_market_pregame()
returns trigger language plpgsql set search_path = '' as $$
begin
  if new.observed_at::timestamptz >= new.kickoff_time::timestamptz then
    raise exception 'NBA market evidence must be strictly before kickoff';
  end if;
  return new;
end;
$$;

drop trigger if exists nba_market_observations_pregame on public.nba_market_observations;
create trigger nba_market_observations_pregame
before insert on public.nba_market_observations for each row
execute function public.validate_nba_market_pregame();

alter table public.nba_games enable row level security;
alter table public.nba_prediction_snapshots enable row level security;
alter table public.nba_market_observations enable row level security;
alter table public.nba_settlements enable row level security;
alter table public.nba_replay_runs enable row level security;

revoke all on table public.nba_games from anon, authenticated;
revoke all on table public.nba_prediction_snapshots from anon, authenticated;
revoke all on table public.nba_market_observations from anon, authenticated;
revoke all on table public.nba_settlements from anon, authenticated;
revoke all on table public.nba_replay_runs from anon, authenticated;

revoke all on function public.reject_nba_immutable_evidence_change() from public, anon, authenticated;
revoke all on function public.validate_nba_pregame_evidence() from public, anon, authenticated;
revoke all on function public.validate_nba_market_pregame() from public, anon, authenticated;
