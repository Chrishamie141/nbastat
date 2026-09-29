-- Account-owned confirmed parlay tickets. Server-only; browser access is denied.
create table if not exists public.parlay_tickets (
  ticket_id text primary key, user_id bigint not null, ticket_type text not null,
  sportsbook text not null, strategy text not null, season integer not null,
  season_type text not null, week integer not null, stake double precision,
  combined_odds integer, potential_payout double precision, number_of_legs integer not null,
  status text not null, created_at text not null, confirmed_at text not null,
  graded_at text, source_hash text not null
);
create table if not exists public.parlay_ticket_legs (
  ticket_leg_id text primary key, ticket_id text not null references public.parlay_tickets(ticket_id),
  leg_index integer not null, game_id text not null, sportsbook text not null,
  player_id text, player_name text, team text, opponent text, matchup text not null,
  market text not null, selection text not null, line double precision, odds integer,
  smartbet_projection double precision, smartbet_probability double precision,
  market_implied_probability double precision, edge double precision, confidence double precision,
  strategy text not null, recommendation_state text not null, model_version text,
  season integer not null, week integer not null, selected_at text not null,
  final_result double precision, leg_status text not null, source_row_id text not null,
  source_snapshot_json text not null, unique(ticket_id,leg_index), unique(ticket_id,source_row_id)
);
create index if not exists idx_parlay_tickets_user_created on public.parlay_tickets(user_id,confirmed_at);
create index if not exists idx_parlay_ticket_legs_game_status on public.parlay_ticket_legs(game_id,leg_status);
alter table public.parlay_tickets enable row level security;
alter table public.parlay_ticket_legs enable row level security;
revoke all on table public.parlay_tickets, public.parlay_ticket_legs from anon, authenticated;
