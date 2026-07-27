# Backtesting snapshots

NFL snapshots use a multi-provider architecture based on The Odds API, ESPN, optional verified NFL data, OpenWeather, and local JSON exports.

## Environment variables

- `THE_ODDS_API_KEY`: The Odds API for NFL h2h, spreads, totals, player props, bookmaker metadata, and historical point-in-time odds when the subscription supports them.
- `OPENWEATHER_API_KEY`: OpenWeather weather by game city/stadium context.

## Provider ownership

- `games.json`: ESPN NFL scoreboard endpoint primarily.
- `odds.json`: The Odds API (`americanfootball_nfl`) with American odds and US bookmakers. Historical odds must use a pre-kickoff snapshot timestamp; authorization/subscription failures are reported and current odds are not substituted.
- `weather.json`: existing OpenWeather integration.
- `injuries.json`: optional verified NFL source or ESPN if usable injury data appears; otherwise an empty optional file is written with a warning unless `--strict` is supplied.
- `player_stats.json`: ESPN summary/box-score data primarily.
- `team_stats.json`: ESPN summary/box-score data primarily.
- `outcomes.json`: ESPN final scores.
- `local-json`: fills historical gaps from exported JSON without calling live APIs.

The optional `nfl-official` adapter is isolated and disabled by default because no dependable supported NFL-hosted JSON endpoint is configured in this repository. Do not scrape HTML as a primary source.

## Examples

Current/live collection:

```bash
python -m backtesting.build_snapshots \
  --league nfl \
  --season 2026 \
  --start-week 1 \
  --end-week 1 \
  --providers odds-api,espn,nfl-official \
  --validate
```

Historical multi-season build (regular season and playoff week numbers are
attempted; unavailable provider data is reported rather than invented):

```bash
python -m backtesting.build_nfl_historical_snapshots \
  --season 2022 --season 2023 --season 2024 --season 2025
python -m backtesting.validate_snapshots --sport nfl
python -m backtesting.nfl_v1_v2_validation
```

The output root is `backtesting/data/nfl/<season>/week_NN`. Real snapshots and
raw caches are intentionally gitignored due to size and licensing. Construction
may contact configured providers, but validation and V1/V2 evaluation read only
the saved JSON files and are offline. Each week has `metadata.json` and
`manifest.json` with source lineage, normalization version, cutoff policy, and
record counts. Never point `BACKTESTING_LOCAL_EXPORT_DIR` at `tests/fixtures`:
those records are synthetic and are not historical evidence.

Strict build:

```bash
python -m backtesting.build_snapshots \
  --league nfl \
  --season 2025 \
  --start-week 1 \
  --end-week 1 \
  --providers odds-api,espn,nfl-official \
  --strict \
  --validate
```

Historical The Odds API access may require a paid subscription. Current responses cannot reconstruct historical point-in-time inputs.

### Historical odds scoping

The historical `/odds` endpoint is a point-in-time view of the NFL market, not
a response scoped to an ESPN week or to one game.  Snapshot construction first
loads ESPN's canonical games and makes one request at `kickoff -
odds-hours-before-kickoff` for each game.  Each response is reconciled against
only that request's canonical game; other Week 1 games and later-season events
are discarded before bookmaker markets are flattened.  Thus an individual
batch normally reports one matched event even when the response contains many
available future events.

The final odds rows retain the canonical ESPN `game_id`, reconciliation proof,
provider event ID, and point-in-time timestamp. Rows are deterministically
deduplicated by canonical game, requested/captured snapshot, bookmaker, market,
selection, line, and price. Set `BACKTESTING_ODDS_DEBUG=1` to print individual
discarded events; normal output prints only aggregate receipt, match, discard,
coverage, and persisted-row counts.

The previously observed 1,042 rows were the sum of the rows flattened from the
single matched canonical event in each per-game response (bookmakers × markets
× outcomes across 16 requested snapshots). The large unmatched counts described
events returned by the point-in-time endpoint but already skipped by event-level
normalization; they were noisy diagnostics, not evidence that those provider
IDs were persisted. The additional per-request boundary, semantic deduplication,
persisted reconciliation metadata, and validator checks now make that property
explicit and enforceable.

## NFL completed-game team history

NFL team-market replays use one `team_stats.json` per snapshot week. The file contains
deduplicated completed-game observations from the preceding regular season plus
already-completed games in the replay season; it does not duplicate history per
target game. Each observation records `season`, `week`, `team`, `game_id`,
`opponent`, `points_for`, `points_against`, optional `home_away`, `completed_at`,
`data_as_of`, `record_role=completed_game_history`, and `source`. Historical
outcomes are deliberately marked `is_pregame=false`; the predictor, rather than a
misleading label, proves usability by requiring both timestamps to precede each
target kickoff.

The former ESPN adapter read each target game's boxscore and produced two
current-game rows with non-scoring boxscore stats. Those rows could not provide
pregame points-for/against and were correctly rejected. The adapter now builds
history from ESPN's free completed scoreboards. Replay remains offline and reads
only the persisted snapshot.

To update only team history in an existing snapshot (preserving `odds.json` and
never constructing an Odds API source), run:

```bash
python -m backtesting.build_snapshots --league nfl --season 2025 --start-week 1 --end-week 1 --refresh team-stats --validate
```

Then replay entirely from local files:

```bash
python -m backtesting.run_backtest --league nfl --season 2025 --start-week 1 --end-week 1 --markets h2h,spreads,totals
```
