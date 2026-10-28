-- Additive, server-only NBA roster and player-availability evidence.
-- This migration does not alter predictions, settlements, users, billing, or NFL tables.
begin;

create table if not exists public.nba_player_availability_snapshots (
  snapshot_id text primary key,
  season_code text not null check (season_code ~ '^20[0-9]{2}-[0-9]{2}$'),
  team_code text not null,
  athlete_id text not null,
  player_name text not null,
  position text,
  jersey text,
  roster_status text not null,
  injury_status text not null,
  availability_status text not null check (availability_status in
    ('OUT','DOUBTFUL','QUESTIONABLE','PROBABLE','AVAILABLE','ROSTER_INACTIVE',
     'NO_INJURY_REPORTED','UNKNOWN')),
  injury_detail text,
  injury_observed_at text,
  provider text not null,
  provider_observed_at text not null,
  retrieved_at text not null,
  source_hash text not null,
  payload_json text not null
);

create index if not exists idx_nba_availability_current
  on public.nba_player_availability_snapshots
  (season_code, team_code, athlete_id, provider_observed_at);

create table if not exists public.nba_data_source_status (
  source_key text primary key,
  season_code text not null check (season_code ~ '^20[0-9]{2}-[0-9]{2}$'),
  data_kind text not null check (data_kind in ('teams','roster','availability','player_stats')),
  team_code text,
  provider text not null,
  last_attempt_at text not null,
  last_success_at text,
  provider_observed_at text,
  max_age_seconds integer not null check (max_age_seconds > 0),
  row_count integer not null default 0 check (row_count >= 0),
  state text not null check (state in ('FRESH','STALE','MISSING','ERROR')),
  error_code text,
  details_json text not null default '{}'
);

create index if not exists idx_nba_source_status_kind
  on public.nba_data_source_status(season_code, data_kind, state);

drop trigger if exists nba_player_availability_snapshots_immutable_update
  on public.nba_player_availability_snapshots;
create trigger nba_player_availability_snapshots_immutable_update
before update on public.nba_player_availability_snapshots for each row
execute function public.reject_nba_immutable_evidence_change();

drop trigger if exists nba_player_availability_snapshots_immutable_delete
  on public.nba_player_availability_snapshots;
create trigger nba_player_availability_snapshots_immutable_delete
before delete on public.nba_player_availability_snapshots for each row
execute function public.reject_nba_immutable_evidence_change();

alter table public.nba_player_availability_snapshots enable row level security;
alter table public.nba_data_source_status enable row level security;

revoke all on table public.nba_player_availability_snapshots
  from anon, authenticated, service_role;
revoke all on table public.nba_data_source_status
  from anon, authenticated, service_role;

insert into public.nba_model_registry
  (model_version,status,created_at,sample_size,metrics_json,production_eligible,
   promotion_blockers_json,notes)
values
  ('nba-game-logit-form-rest-research-v2','RESEARCH_ONLY',now()::text,0,'{}',0,
   '["prospective_shadow_validation_required","independent_reproduction_required","market_benchmark_required","manual_promotion_review_required"]',
   'Offline form/rest candidate; not wired to prediction generation or wagering.')
on conflict (model_version) do nothing;

commit;
