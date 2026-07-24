# NFL 2025 Week 1 local provider export templates

Place real provider exports under `BACKTESTING_LOCAL_EXPORT_DIR/nfl/2025/week_01/`.
Supported files are `odds.json`, `weather.json`, `injuries.json`, or a combined `snapshot.json` with dataset keys.

Records must include `source`, `captured_at`, `data_as_of`, `is_pregame`, `game_id`, `season`, and `week`. Pregame records must have `captured_at` and `data_as_of` before kickoff. Do not commit copyrighted bulk provider data.
