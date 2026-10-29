begin;

set local lock_timeout = '5s';
do $$
declare
  prediction_owner text;
begin
  select tableowner into prediction_owner
  from pg_tables
  where schemaname = 'public' and tablename = 'nfl_game_predictions';
  if prediction_owner is null then
    raise exception 'nfl_game_predictions is required before immutability migration';
  end if;
  if prediction_owner <> current_user then
    execute format('grant %I to %I with set true', prediction_owner, current_user);
    execute format('set local role %I', prediction_owner);
  end if;
end
$$;

create or replace function public.protect_nfl_system_prediction()
returns trigger
language plpgsql
set search_path = pg_catalog, public
as $$
begin
  if tg_op = 'DELETE' then
    if old.user_id = 0 then
      raise exception using
        errcode = '55000',
        message = 'official NFL system predictions are immutable';
    end if;
    return old;
  end if;

  if old.user_id = 0 or new.user_id = 0 then
    if new.id is distinct from old.id
       or new.user_id is distinct from old.user_id
       or new.game_id is distinct from old.game_id
       or new.season is distinct from old.season
       or new.week is distinct from old.week
       or new.kickoff_time is distinct from old.kickoff_time
       or new.generated_at is distinct from old.generated_at
       or new.model_version is distinct from old.model_version
       or new.prediction_json is distinct from old.prediction_json then
      raise exception using
        errcode = '55000',
        message = 'official NFL system prediction content is immutable';
    end if;

    if old.kickoff_time::timestamptz <= now()
       and to_jsonb(new) is distinct from to_jsonb(old) then
      raise exception using
        errcode = '55000',
        message = 'historical NFL system predictions are immutable';
    end if;
  end if;

  return new;
end
$$;

drop trigger if exists nfl_system_prediction_immutable
  on public.nfl_game_predictions;
create trigger nfl_system_prediction_immutable
before update or delete on public.nfl_game_predictions
for each row execute function public.protect_nfl_system_prediction();

revoke all on function public.protect_nfl_system_prediction() from public;

reset role;

commit;
