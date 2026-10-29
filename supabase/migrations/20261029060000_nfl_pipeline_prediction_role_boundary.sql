-- Permit only the environment-specific NFL refresh roles to read and create
-- frozen system predictions. Updates and deletes remain unavailable, while the
-- immutability trigger separately protects official prediction content.
begin;

do $$
declare
  role_name text;
  prediction_sequence text;
begin
  if to_regclass('public.nfl_game_predictions') is null then
    raise exception 'nfl_game_predictions is required before role boundary migration';
  end if;

  prediction_sequence := pg_get_serial_sequence(
    'public.nfl_game_predictions', 'id'
  );

  foreach role_name in array array[
    'smartbets_staging_app',
    'smartbets_production_app'
  ]
  loop
    if exists (select 1 from pg_roles where rolname = role_name) then
      execute format('grant usage on schema public to %I', role_name);
      execute format(
        'grant select,insert on public.nfl_game_predictions to %I',
        role_name
      );
      if prediction_sequence is not null then
        execute format(
          'grant usage,select on sequence %s to %I',
          prediction_sequence,
          role_name
        );
      end if;
    end if;
  end loop;
end
$$;

drop policy if exists nfl_predictions_pipeline_read
  on public.nfl_game_predictions;
create policy nfl_predictions_pipeline_read
  on public.nfl_game_predictions
  for select to public
  using (current_user in ('smartbets_staging_app','smartbets_production_app'));

drop policy if exists nfl_predictions_pipeline_insert
  on public.nfl_game_predictions;
create policy nfl_predictions_pipeline_insert
  on public.nfl_game_predictions
  for insert to public
  with check (
    current_user in ('smartbets_staging_app','smartbets_production_app')
    and user_id = 0
  );

commit;
