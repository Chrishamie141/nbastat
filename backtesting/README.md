# Internal backtesting snapshots

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

## Required environment variables

Live/provider integrations remain optional for internal development:

- `THE_ODDS_API_KEY` or `ODDS_API_KEY` enables current The Odds API NFL odds helpers.
- `SPORTSDATAIO_API_KEY` or `SPORTS_DATA_IO_API_KEY` enables SportsDataIO NFL scores/stats/injury helpers.
- `OPENWEATHER_API_KEY` or `OPEN_WEATHER_API_KEY` enables OpenWeather current weather helpers.
- `BACKTESTING_LOCAL_EXPORT_DIR` points the snapshot builder at local historical JSON exports. It defaults to `backtesting/data/provider_exports`.

The snapshot builder redacts known API key values from progress and error output.

## Supported providers and limitations

The repository currently contains NFL client/helper logic in `nfl_data_service.py` for:

- The Odds API: current NFL games, team lines, and player props.
- SportsDataIO: current-season player game stats and injuries helper hooks; final-result helpers intentionally return empty data unless a completed-game provider is configured.
- OpenWeather: current weather by city/game location.
- Deterministic sample fallback data for the public NFL flow.

These existing helpers are **not** treated as true historical snapshot sources by `python -m backtesting.build_snapshots`. Current endpoints and fallback samples would create future-data leakage or fabricated history, so the default `existing-nfl` adapter reports that true historical coverage is unavailable. Complete 2025 NFL snapshots require either:

1. local exported historical JSON files, or
2. a paid/official historical provider adapter that preserves point-in-time `captured_at` values.

Do not claim historical odds, injuries, or weather support unless the configured provider/export actually contains historical point-in-time records.

## Historical snapshot builder

Build weeks from local provider exports and validate after writing:

```bash
python -m backtesting.build_snapshots \
  --league nfl \
  --season 2025 \
  --start-week 1 \
  --end-week 18 \
  --providers local-json \
  --validate
```

Default snapshot output is `backtesting/data/snapshots`. Override it with `--data-dir`:

```bash
python -m backtesting.build_snapshots --league nfl --season 2025 --start-week 1 --end-week 1 --data-dir /tmp/snapshots --providers local-json --validate
```

Resume a partially built season. Complete, valid weeks are skipped; incomplete weeks continue:

```bash
python -m backtesting.build_snapshots --league nfl --season 2025 --start-week 1 --end-week 18 --resume --validate
```

Overwrite existing files only when intended:

```bash
python -m backtesting.build_snapshots --league nfl --season 2025 --start-week 1 --end-week 1 --overwrite --validate
```

Dry-run fetches/normalizes and prints counts without writing snapshot files:

```bash
python -m backtesting.build_snapshots --league nfl --season 2025 --start-week 1 --end-week 1 --dry-run
```

Use `--strict` to fail a week when any required or selected dataset is unavailable/invalid. Without `--strict`, available datasets are written, optional missing datasets are written as empty JSON arrays, and limitations are printed as warnings.

Raw provider responses are cached under `backtesting/data/raw_cache/<provider>/<league>/<season>/week_##/` to avoid repeated paid API requests. Cached responses are reused unless `--overwrite` is supplied.

## Local JSON export layout

The `local-json` source reads either per-dataset JSON files or one combined `snapshot.json` file from:

```text
$BACKTESTING_LOCAL_EXPORT_DIR/nfl/2025/week_01/games.json
$BACKTESTING_LOCAL_EXPORT_DIR/nfl/2025/week_01/odds.json
...
```

or:

```text
$BACKTESTING_LOCAL_EXPORT_DIR/nfl/2025/week_01/snapshot.json
```

Combined files should map dataset names (`games`, `odds`, `weather`, `injuries`, `player_stats`, `team_stats`, `outcomes`) to lists of records.

## Importing local JSON/CSV

Import local JSON and write normalized snapshots:

```bash
python -m backtesting.import_historical_data --league nfl --season 2025 --week 1 --source export.json --format json
```

Import local CSV. CSV rows must include a `dataset` column naming one of the snapshot files without `.json`:

```bash
python -m backtesting.import_historical_data --league nfl --season 2025 --week 1 --source export.csv --format csv
```

Existing snapshot files are not overwritten unless `--overwrite` is supplied.

## Validation workflow

Validate all available weeks:

```bash
python -m backtesting.import_historical_data --league nfl --season 2025 --validate-only
```

Validate one week:

```bash
python -m backtesting.import_historical_data --league nfl --season 2025 --week 1 --validate-only
```

Validation checks required files, malformed records, duplicate game IDs, unknown game references from odds/outcomes, unsupported markets, wrong league/season/week records, games without outcomes, and pregame future-data leakage (`captured_at` after kickoff or stats through the replay week or later).

## Backtest workflow

After importing or building real historical snapshots, run a replay with:

```bash
python -m backtesting.run_backtest --league nfl --season 2025 --start-week 1 --end-week 1 --markets PASS_YDS
```

A one-week validation command for the currently missing Week 1 folder is:

```bash
python -m backtesting.import_historical_data --league nfl --season 2025 --week 1 --validate-only
```

## Historical integrity warning

Prediction-time datasets (`odds`, `injuries`, `weather`, `player_stats`, and `team_stats`) must contain only information available before kickoff. Preserve `captured_at` timestamps wherever supported. Do not use final game results, later-season aggregates, current stats, closing lines captured after kickoff, or deterministic fallback samples as historical pregame input. Outcomes are separate and must only be used for grading.
