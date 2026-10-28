-- Reconcile production NBA governance objects that were created previously by
-- application bootstrap rather than by the checked-in Supabase migrations.
--
-- This migration is additive and idempotent. It never updates or deletes
-- application rows. Existing data is checked before every new invariant is
-- installed so a violation aborts the transaction without a partial change.
-- Only this migration's version may be recorded as applied; older migration
-- files must not be backfilled into the ledger merely because similar objects
-- already exist.

set local lock_timeout = '5s';
set local statement_timeout = '60s';

do $$
begin
  if to_regclass('public.nba_games') is null
     or to_regclass('public.nba_prediction_snapshots') is null
     or to_regclass('public.nba_market_observations') is null
     or to_regclass('public.nba_settlements') is null
     or to_regclass('public.nba_replay_runs') is null
     or to_regclass('public.nba_model_registry') is null then
    raise exception 'NBA reconciliation prerequisites are missing';
  end if;

  if exists (
    select 1 from public.nba_games
    where season_code !~ '^20[0-9]{2}-[0-9]{2}$'
       or status not in ('SCHEDULED','LIVE','FINAL','POSTPONED','CANCELED')
  ) then
    raise exception 'existing nba_games rows violate proposed constraints';
  end if;

  if exists (
    select 1 from public.nba_prediction_snapshots
    where (model_probability is not null and (model_probability < 0 or model_probability > 1))
       or model_version not like 'nba-%'
       or generated_at::timestamptz >= kickoff_time::timestamptz
       or feature_cutoff::timestamptz > generated_at::timestamptz
  ) then
    raise exception 'existing NBA prediction rows violate proposed constraints';
  end if;

  if exists (
    select 1 from public.nba_market_observations
    where validation_status not in ('VALID_PREGAME','POST_TIPOFF','MALFORMED','UNATTRIBUTED','STALE')
       or captured_at is null
       or observed_at::timestamptz >= kickoff_time::timestamptz
  ) then
    raise exception 'existing NBA market rows violate proposed constraints';
  end if;

  if exists (
    select 1 from public.nba_settlements
    where status not in ('WIN','LOSS','PUSH','VOID','UNRESOLVED')
  ) then
    raise exception 'existing NBA settlement rows violate proposed constraints';
  end if;

  if exists (
    select 1 from public.nba_replay_runs
    where model_version not like 'nba-%'
       or status not in ('RUNNING','COMPLETE','FAILED')
  ) then
    raise exception 'existing NBA replay rows violate proposed constraints';
  end if;

  if exists (
    select 1 from public.nba_model_registry
    where model_version not like 'nba-%'
       or status not in ('RESEARCH_ONLY','CHALLENGER','CHAMPION','RETIRED')
       or production_eligible not in (0, 1)
  ) then
    raise exception 'existing NBA model registry rows violate proposed constraints';
  end if;
end
$$;
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

do $$
begin
  if not exists (select 1 from pg_constraint where conrelid='public.nba_games'::regclass and conname='nba_games_season_code_check') then
    alter table public.nba_games add constraint nba_games_season_code_check check (season_code ~ '^20[0-9]{2}-[0-9]{2}$') not valid;
  end if;
  if not exists (select 1 from pg_constraint where conrelid='public.nba_games'::regclass and conname='nba_games_status_check') then
    alter table public.nba_games add constraint nba_games_status_check check (status in ('SCHEDULED','LIVE','FINAL','POSTPONED','CANCELED')) not valid;
  end if;
  if not exists (select 1 from pg_constraint where conrelid='public.nba_prediction_snapshots'::regclass and conname='nba_prediction_snapshots_model_probability_check') then
    alter table public.nba_prediction_snapshots add constraint nba_prediction_snapshots_model_probability_check check (model_probability is null or (model_probability >= 0 and model_probability <= 1)) not valid;
  end if;
  if not exists (select 1 from pg_constraint where conrelid='public.nba_prediction_snapshots'::regclass and conname='nba_prediction_snapshots_model_version_check') then
    alter table public.nba_prediction_snapshots add constraint nba_prediction_snapshots_model_version_check check (model_version like 'nba-%') not valid;
  end if;
  if not exists (select 1 from pg_constraint where conrelid='public.nba_market_observations'::regclass and conname='nba_market_observations_validation_status_check') then
    alter table public.nba_market_observations add constraint nba_market_observations_validation_status_check check (validation_status in ('VALID_PREGAME','POST_TIPOFF','MALFORMED','UNATTRIBUTED','STALE')) not valid;
  end if;
  if not exists (select 1 from pg_constraint where conrelid='public.nba_settlements'::regclass and conname='nba_settlements_status_check') then
    alter table public.nba_settlements add constraint nba_settlements_status_check check (status in ('WIN','LOSS','PUSH','VOID','UNRESOLVED')) not valid;
  end if;
  if not exists (select 1 from pg_constraint where conrelid='public.nba_replay_runs'::regclass and conname='nba_replay_runs_model_version_check') then
    alter table public.nba_replay_runs add constraint nba_replay_runs_model_version_check check (model_version like 'nba-%') not valid;
  end if;
  if not exists (select 1 from pg_constraint where conrelid='public.nba_replay_runs'::regclass and conname='nba_replay_runs_status_check') then
    alter table public.nba_replay_runs add constraint nba_replay_runs_status_check check (status in ('RUNNING','COMPLETE','FAILED')) not valid;
  end if;
  if not exists (select 1 from pg_constraint where conrelid='public.nba_model_registry'::regclass and conname='nba_model_registry_model_version_check') then
    alter table public.nba_model_registry add constraint nba_model_registry_model_version_check check (model_version like 'nba-%') not valid;
  end if;
  if not exists (select 1 from pg_constraint where conrelid='public.nba_model_registry'::regclass and conname='nba_model_registry_status_check') then
    alter table public.nba_model_registry add constraint nba_model_registry_status_check check (status in ('RESEARCH_ONLY','CHALLENGER','CHAMPION','RETIRED')) not valid;
  end if;
  if not exists (select 1 from pg_constraint where conrelid='public.nba_model_registry'::regclass and conname='nba_model_registry_production_eligible_check') then
    alter table public.nba_model_registry add constraint nba_model_registry_production_eligible_check check (production_eligible in (0,1)) not valid;
  end if;
end
$$;

alter table public.nba_games validate constraint nba_games_season_code_check;
alter table public.nba_games validate constraint nba_games_status_check;
alter table public.nba_prediction_snapshots validate constraint nba_prediction_snapshots_model_probability_check;
alter table public.nba_prediction_snapshots validate constraint nba_prediction_snapshots_model_version_check;
alter table public.nba_market_observations validate constraint nba_market_observations_validation_status_check;
alter table public.nba_market_observations alter column captured_at set not null;
alter table public.nba_settlements validate constraint nba_settlements_status_check;
alter table public.nba_replay_runs validate constraint nba_replay_runs_model_version_check;
alter table public.nba_replay_runs validate constraint nba_replay_runs_status_check;
alter table public.nba_model_registry validate constraint nba_model_registry_model_version_check;
alter table public.nba_model_registry validate constraint nba_model_registry_status_check;
alter table public.nba_model_registry validate constraint nba_model_registry_production_eligible_check;

create or replace function public.reject_nba_immutable_evidence_change()
returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  raise exception 'immutable NBA evidence';
end
$$;

create or replace function public.validate_nba_pregame_evidence()
returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if new.generated_at::timestamptz >= new.kickoff_time::timestamptz
     or new.feature_cutoff::timestamptz > new.generated_at::timestamptz then
    raise exception 'NBA prediction evidence must be frozen before kickoff';
  end if;
  return new;
end
$$;

create or replace function public.validate_nba_market_pregame()
returns trigger
language plpgsql
set search_path = pg_catalog
as $$
begin
  if new.observed_at::timestamptz >= new.kickoff_time::timestamptz then
    raise exception 'NBA market evidence must be strictly before kickoff';
  end if;
  return new;
end
$$;

revoke all on function public.reject_nba_immutable_evidence_change() from public;
revoke all on function public.validate_nba_pregame_evidence() from public;
revoke all on function public.validate_nba_market_pregame() from public;

do $$
begin
  if not exists (select 1 from pg_trigger where tgrelid='public.nba_prediction_snapshots'::regclass and tgname='nba_prediction_snapshots_immutable_update' and not tgisinternal) then
    create trigger nba_prediction_snapshots_immutable_update before update on public.nba_prediction_snapshots for each row execute function public.reject_nba_immutable_evidence_change();
  end if;
  if not exists (select 1 from pg_trigger where tgrelid='public.nba_prediction_snapshots'::regclass and tgname='nba_prediction_snapshots_immutable_delete' and not tgisinternal) then
    create trigger nba_prediction_snapshots_immutable_delete before delete on public.nba_prediction_snapshots for each row execute function public.reject_nba_immutable_evidence_change();
  end if;
  if not exists (select 1 from pg_trigger where tgrelid='public.nba_market_observations'::regclass and tgname='nba_market_observations_immutable_update' and not tgisinternal) then
    create trigger nba_market_observations_immutable_update before update on public.nba_market_observations for each row execute function public.reject_nba_immutable_evidence_change();
  end if;
  if not exists (select 1 from pg_trigger where tgrelid='public.nba_market_observations'::regclass and tgname='nba_market_observations_immutable_delete' and not tgisinternal) then
    create trigger nba_market_observations_immutable_delete before delete on public.nba_market_observations for each row execute function public.reject_nba_immutable_evidence_change();
  end if;
  if not exists (select 1 from pg_trigger where tgrelid='public.nba_prediction_snapshots'::regclass and tgname='nba_prediction_snapshots_pregame' and not tgisinternal) then
    create trigger nba_prediction_snapshots_pregame before insert on public.nba_prediction_snapshots for each row execute function public.validate_nba_pregame_evidence();
  end if;
  if not exists (select 1 from pg_trigger where tgrelid='public.nba_market_observations'::regclass and tgname='nba_market_observations_pregame' and not tgisinternal) then
    create trigger nba_market_observations_pregame before insert on public.nba_market_observations for each row execute function public.validate_nba_market_pregame();
  end if;
end
$$;

do $$
declare
  table_name text;
  role_name text;
begin
  foreach table_name in array array[
    'users',
    'stripe_webhook_events',
    'parlay_history',
    'password_reset_tokens',
    'auth_bootstrap',
    'operator_actions',
    'nba_games',
    'nba_prediction_snapshots',
    'nba_market_observations',
    'nba_settlements',
    'nba_replay_runs',
    'nba_model_registry',
    'nba_operational_settings',
    'nba_automation_runs'
  ]
  loop
    if to_regclass(format('public.%I', table_name)) is not null then
      execute format('alter table public.%I enable row level security', table_name);
      foreach role_name in array array['anon','authenticated','service_role']
      loop
        if exists (select 1 from pg_roles where pg_roles.rolname = role_name) then
          execute format('revoke all privileges on table public.%I from %I', table_name, role_name);
        end if;
      end loop;
    end if;
  end loop;
end
$$;

