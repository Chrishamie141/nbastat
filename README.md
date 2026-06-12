# NBA Team Matchup Prediction Engine

This project predicts player box-score stats using `nba_api` + `scikit-learn` Random Forest models, now with a **team auto-roster workflow**.

## What it does
- Single-player prediction mode.
- Team mode: enter only `LAL`, `OKC`, `BOS`, etc.
- Auto-fetches roster via `CommonTeamRoster`.
- Attempts next-opponent detection and home/away context.
- Computes model prediction + opponent averages + blended prediction.
- Ranks top reliable and top raw predictions for PTS/REB/AST/STL/BLK.
- Saves every printed player prediction to `data/prediction_history.csv`.
- Grades completed games against the NBA box score and reports MAE/RMSE/range-hit accuracy by stat.

## Run
```bash
pip install -r requirements.txt
python app.py
```

## Normal menu usage

Run `python app.py` for the interactive menu. Normal use shows only user-facing prediction and grading workflows:

1. Single Player Prediction
2. Default `roster.txt` Prediction
3. Team Auto-Roster Prediction
4. Best Bets Report
5. Auto Parlay Builder
6. Grade Predictions


## Background cache healing

On normal startup, the app runs a lightweight cache health check before showing the menu. The check creates `data/cache` if needed, removes cache files with invalid filenames, validates roster and prediction cache payloads, and automatically deletes only invalid/bad cache files. When nothing is wrong, startup stays quiet; if anything is fixed, the app prints a short summary such as `Startup health check: removed 2 invalid cache file(s).`

Roster lookup diagnostics also run automatically only when a lookup fails. The app reports the normalized team abbreviation, live lookup attempts, error type, cache usage, invalid/deleted cache state, and sample cached players when available. Betting workflows automatically clear bad roster or prediction cache files and continue with live data or valid cached predictions when possible.

## Optional maintenance flags

Maintenance and debug actions are available as command-line flags instead of numbered menu options:

```bash
python app.py --clear-cache
python app.py --debug-roster NYK
python app.py --health-check
```

- `--clear-cache` deletes cache files and exits while preserving `.gitignore` and `.gitkeep`.
- `--debug-roster TEAM` runs roster diagnostics for one team and exits.
- `--health-check` runs the cache health check, prints a clean/fixed summary, and exits.

## Team auto-roster mode
1. Select mode `3`.
2. Enter team abbreviation.
3. App pulls roster, finds next opponent (fallback: General Estimate), predicts each player, saves prediction history rows, and prints rankings.

## Prediction history and grading
- Prediction history is stored in `data/prediction_history.csv`.
- One row is saved for each printed player/stat prediction: PTS, REB, AST, STL, and BLK.
- Existing model training remains unchanged; this history is only for post-game evaluation.
- Select mode `6` and choose the legacy NBA `game_id` option after a game is complete to pull the actual box score and grade matching ungraded predictions.
- Select mode `6` and choose the accuracy report option to print the model accuracy report across all graded predictions.

## Opponent-specific logic
- Pulls regular + playoff logs.
- Filters games vs upcoming opponent.
- Computes opponent-specific averages with sample size.

## Blended logic
- 3+ games vs opponent: `60% ML + 40% opponent average`
- 1-2 games: `75% ML + 25% opponent average`
- 0 games: `ML only`

## Confidence scoring
- `normalized_error = mae / max(prediction, 1)`
- `score = max(0, round(100 - normalized_error * 100, 1))`
- Labels: HIGH (80+), MEDIUM (60-79), LOW (<60)

## Limitations
- NBA endpoints can be slow/unavailable; safe fallback is used for schedule context.
- STL/BLK are inherently noisy.
- Early season data can produce unstable confidence.
