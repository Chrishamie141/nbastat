# Internal backtesting snapshots

This package is developer-only. Do not add frontend pages, public API routes, or user-facing access to these tools.

## Expected folder structure

Historical snapshots are read from `backtesting/data/snapshots` using lower-case leagues, string seasons, and zero-padded week folders:

```text
backtesting/data/snapshots/
  nfl/
    2025/
      week_01/
        games.json
        odds.json
        weather.json
        injuries.json
        player_stats.json
        team_stats.json
        outcomes.json
```

Week input can be `1` or `01`, but snapshots are written as `week_01`, `week_02`, ... `week_18`.

## Normalized schemas

Each file contains a JSON array of records with these fields:

- `games.json`: `game_id`, `league`, `season`, `week`, `kickoff_time`, `home_team`, `away_team`, `venue`, `status`
- `odds.json`: `game_id`, `market`, `selection`, `line`, `odds`, `sportsbook`, `captured_at`
- `weather.json`: `game_id`, `captured_at`, `temperature`, `wind_speed`, `precipitation`, `conditions`
- `injuries.json`: `team`, `player`, `position`, `status`, `captured_at`
- `player_stats.json`: `player`, `team`, `season`, `through_week`, `stats`
- `team_stats.json`: `team`, `season`, `through_week`, `stats`
- `outcomes.json`: `game_id`, `final_home_score`, `final_away_score`, `player_results`, `market_results`, `completed_at`

Validation reports missing weeks, missing games/outcomes files, malformed records, games without matching outcomes, odds without matching games, and unsupported markets.

## Import examples

Import local JSON and write normalized snapshots:

```bash
python -m backtesting.import_historical_data \
  --league nfl \
  --season 2025 \
  --week 1 \
  --source ./historical_raw/nfl_week1.json \
  --format json
```

Import local CSV. CSV rows must include a `dataset` column naming one of the snapshot files without `.json`:

```bash
python -m backtesting.import_historical_data \
  --league nfl \
  --season 2025 \
  --week 1 \
  --source ./historical_raw/nfl_week1.csv \
  --format csv
```

Existing snapshot files are not overwritten unless `--overwrite` is supplied.

## Validation examples

Validate a whole imported season:

```bash
python -m backtesting.import_historical_data \
  --league nfl \
  --season 2025 \
  --validate-only
```

Validate one week:

```bash
python -m backtesting.import_historical_data \
  --league nfl \
  --season 2025 \
  --week 1 \
  --validate-only
```

## Fixture test command

The deterministic fixture under `tests/fixtures/backtesting/nfl/2025/week_01` is test-only data and is intentionally tiny. It exists only to verify the full replay pipeline can produce at least one prediction and one gradeable result:

```bash
PYTHONPATH=. pytest tests/test_backtesting_framework.py -q
```

## Real backtest command

After importing real historical snapshots, run a replay with:

```bash
python -m backtesting.run_backtest \
  --league nfl \
  --season 2025 \
  --start-week 1 \
  --end-week 18 \
  --markets PASS_YDS
```

Historical odds must reflect only information available before kickoff. Do not use closing or post-game lines as pre-kickoff snapshots unless they were actually captured before the relevant kickoff.
