-- SmartBetSports accesses these internal tables through its dedicated direct
-- PostgreSQL role. They are not browser-facing Supabase Data API resources.
-- RLS intentionally has no anon/authenticated policies: grants determine that
-- the API roles cannot reach the tables, while the owning app role keeps the
-- existing server workflow intact (RLS is deliberately not forced on owners).
do $$
declare
  table_name text;
begin
  foreach table_name in array array[
    'users',
    'stripe_webhook_events',
    'parlay_history',
    'smartbets_schema_migrations'
  ]
  loop
    if to_regclass(format('public.%I', table_name)) is not null then
      execute format('alter table public.%I enable row level security', table_name);
      execute format(
        'revoke all privileges on table public.%I from anon, authenticated, service_role',
        table_name
      );
    end if;
  end loop;
end
$$;
