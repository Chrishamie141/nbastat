# NFL historical player-prop pricing capability audit

## Provider capability

The configured sportsbook provider is **The Odds API**. ESPN, NFL official,
weather, and local adapters do not acquire sportsbook player prices. The six
approved provider keys and canonical mappings are:

| Provider market key | Canonical market | Historical availability |
|---|---|---|
| `player_pass_yds` | `passing_yards` | account/date/event dependent; verify |
| `player_pass_tds` | `passing_tds` | account/date/event dependent; verify |
| `player_rush_attempts` | `rushing_attempts` | account/date/event dependent; verify |
| `player_rush_yds` | `rushing_yards` | account/date/event dependent; verify |
| `player_receptions` | `receptions` | account/date/event dependent; verify |
| `player_reception_yds` | `receiving_yards` | account/date/event dependent; verify |

These are event-specific markets. They must be requested from the historical
event-odds endpoint rather than mixed into the sport-level team-odds request.
Bookmaker coverage is whatever the selected region returned at that historical
snapshot; it must not be inferred. Snapshot time and each bookmaker/market
`last_update` are retained. Earliest accessible history and exact billing are
subscription dependent and remain **unverified** until the safe one-event check
is explicitly authorized. Planning uses the conservative documented historical
multiplier formula of `10 × regions × markets` cost units per cache miss.

Provider references: [historical odds](https://the-odds-api.com/liveapi/guides/v4/#historical-odds),
[event odds](https://the-odds-api.com/liveapi/guides/v4/#get-event-odds), and
[market keys](https://the-odds-api.com/sports-odds-data/betting-markets.html).

## Checkout cache audit

The repository's `backtesting/data` snapshot root contains no historical JSON
snapshots or raw player-prop responses. The checked-in test snapshots contain
team odds only. Consequently Weeks 1–6 have zero genuine prop rows, games,
players, books, or covered weeks; all six line and price statuses are
`NOT_READY`. The offline CLI produces the authoritative report for a populated
deployment without contacting a provider:

```bash
python -m backtesting.audit_nfl_player_prop_odds \
  --snapshot-root backtesting/data/snapshots \
  --season 2025 --start-week 1 --end-week 6
```

Store normalized rows separately at each week's
`odds_player_props.json`; do not rewrite `odds.json`. Existing team snapshots
therefore remain backward compatible. Raw provider cache payloads are audited
and reused before acquisition, but raw names are not gradeable until reconciled.

## Safety, identity, pricing, and readiness

The canonical quote contains league, season, week, canonical game and player
identity, display name/team, canonical market, side, line, American and decimal
price, implied probability, bookmaker, snapshot/market/data timestamps, source,
and provider event ID. Both sides remain distinct rows and are paired by game,
player, market, bookmaker, exact line, and snapshot.

Stable canonical player IDs take priority. Otherwise controlled name matching is
restricted by canonical game and team. Unknown, ambiguous, duplicate-name, and
team-mismatch cases are rejected rather than silently attached. Grading requires
the exact canonical game/player/market triple and supports pushes.

Player quotes use the shared team-market prediction cutoff helper. Invalid
timestamps and any snapshot or market update after the authoritative cutoff are
rejected. Consensus is calculated only among books quoting the exact same line;
best OVER and UNDER preserve their execution bookmaker. No-vig probability
requires a paired line.

Feature and outcome readiness remains `READY`. Genuine historical lines and
prices are currently `NOT_READY`, so model-vs-book metrics, calibration, ROI,
and units must not be reported. Coverage becomes `PARTIAL` when only some
weeks/books exist. Unsupported prop markets remain explicit and are not fetched.

Individual leg prices are **not** historical SGP ticket prices.
`HISTORICAL_SGP_BOOK_PRICE_READY = NOT_READY`. Simulation may publish a clearly
labelled model fair decimal/American price from joint probability, but it is not
sportsbook EV. A future current-price adapter may compare that fair probability
with a genuine offered SGP quote; no bet-placement interface is included.

Review a one-event capability check (no network by default):

```bash
python -m backtesting.verify_player_prop_provider \
  --event-id PROVIDER_EVENT_ID --date 2025-09-07T12:00:00Z
```

Only after reviewing the printed quota estimate, setting the API key, and adding
`--acknowledge-quota` will it make exactly one request. Acquire historical prop
odds before building a final SGP selector; otherwise individual-bet evaluation
and sportsbook SGP comparison have no genuine execution prices.
