-- Additive NBA operational recovery state. No historical evidence is rewritten.
alter table public.nba_games
  add column if not exists season_phase text not null default 'unknown';

create table if not exists public.nba_operational_settings (
  singleton integer primary key check (singleton = 1),
  enabled integer not null default 1 check (enabled in (0, 1)),
  season_override text,
  updated_at text not null,
  updated_by text not null default 'system'
);

insert into public.nba_operational_settings
  (singleton, enabled, updated_at, updated_by)
values (1, 1, now()::text, 'migration')
on conflict (singleton) do nothing;

create table if not exists public.nba_automation_runs (
  run_id text primary key,
  season_code text not null,
  started_at text not null,
  completed_at text,
  status text not null check (status in ('RUNNING', 'SUCCEEDED', 'FAILED')),
  trigger_source text not null,
  games_fetched integer not null default 0,
  games_changed integer not null default 0,
  predictions_created integer not null default 0,
  predictions_settled integer not null default 0,
  predictions_pending integer not null default 0,
  error_code text,
  details_json text not null default '{}'
);

create index if not exists idx_nba_automation_runs_started
  on public.nba_automation_runs(started_at, status);

insert into public.nba_model_registry
  (model_version,status,created_at,sample_size,metrics_json,production_eligible,
   promotion_blockers_json,notes)
values
  ('nba-game-elo-baseline-v1','RESEARCH_ONLY',now()::text,0,'{}',0,
   '["chronological_validation_required","minimum_sample_not_met","calibration_not_established","profitability_not_established","manual_promotion_review_required"]',
   'Game-winner Elo baseline; research-only until independently validated.')
on conflict (model_version) do nothing;

alter table public.nba_operational_settings enable row level security;
alter table public.nba_automation_runs enable row level security;
revoke all on table public.nba_operational_settings from anon, authenticated;
revoke all on table public.nba_automation_runs from anon, authenticated;
