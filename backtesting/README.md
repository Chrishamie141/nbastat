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

Historical build:

```bash
python -m backtesting.build_snapshots \
  --league nfl \
  --season 2025 \
  --start-week 1 \
  --end-week 18 \
  --providers odds-api,espn,nfl-official,local-json \
  --resume \
  --validate
```

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
