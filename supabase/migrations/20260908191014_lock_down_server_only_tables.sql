-- These tables are used only by the SmartBetSports server connection.  They
-- intentionally have no anon/authenticated policies and are not browser APIs.
alter table public.password_reset_tokens enable row level security;
alter table public.auth_bootstrap enable row level security;
alter table public.operator_actions enable row level security;

revoke all on table public.password_reset_tokens from anon, authenticated;
revoke all on table public.auth_bootstrap from anon, authenticated;
revoke all on table public.operator_actions from anon, authenticated;
