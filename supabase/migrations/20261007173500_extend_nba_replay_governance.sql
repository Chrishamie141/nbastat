-- Additive NBA replay provenance and model-governance metadata.
do $$
declare legacy_unique record;
begin
  for legacy_unique in
    select conname from pg_constraint
    where conrelid='public.nba_prediction_snapshots'::regclass
      and contype='u'
      and pg_get_constraintdef(oid) ilike
        '%(user_id, game_id, market, selection, line, model_version)%'
  loop
    execute format('alter table public.nba_prediction_snapshots drop constraint %I', legacy_unique.conname);
  end loop;
end $$;

alter table public.nba_market_observations
  add column if not exists validation_status text not null default 'VALID_PREGAME',
  add column if not exists validation_reason text,
  add column if not exists captured_at text,
  add column if not exists provenance_json text not null default '{}';

update public.nba_market_observations
set captured_at=observed_at
where captured_at is null;

alter table public.nba_market_observations
  alter column captured_at set not null;

alter table public.nba_market_observations
  drop constraint if exists nba_market_observations_validation_status_check;
alter table public.nba_market_observations
  add constraint nba_market_observations_validation_status_check
  check (validation_status in ('VALID_PREGAME','POST_TIPOFF','MALFORMED','UNATTRIBUTED','STALE'));

alter table public.nba_settlements drop constraint if exists nba_settlements_status_check;
update public.nba_settlements set status='WIN' where status='WON';
update public.nba_settlements set status='LOSS' where status='LOST';
alter table public.nba_settlements add constraint nba_settlements_status_check
  check (status in ('WIN','LOSS','PUSH','VOID','UNRESOLVED'));

create table if not exists public.nba_model_registry (
  model_version text primary key check (model_version like 'nba-%'),
  status text not null check (status in ('RESEARCH_ONLY','CHALLENGER','CHAMPION','RETIRED')),
  created_at text not null,
  evaluated_at text,
  evaluation_start text,
  evaluation_end text,
  sample_size integer not null default 0,
  metrics_json text not null default '{}',
  production_eligible integer not null default 0 check (production_eligible in (0,1)),
  promotion_blockers_json text not null default '[]',
  notes text
);

insert into public.nba_model_registry
  (model_version,status,created_at,sample_size,metrics_json,production_eligible,
   promotion_blockers_json,notes)
values
  ('nba-player-stat-v1','RESEARCH_ONLY',now()::text,0,'{}',0,
   '["chronological_validation_required","minimum_sample_not_met","calibration_not_established","profitability_not_established"]',
   'Infrastructure baseline; not an approved production model.')
on conflict (model_version) do nothing;

alter table public.nba_model_registry enable row level security;
revoke all on table public.nba_model_registry from anon, authenticated;
