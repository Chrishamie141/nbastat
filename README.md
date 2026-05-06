# NBA Team Matchup Prediction Engine

This project predicts player box-score stats using `nba_api` + `scikit-learn` Random Forest models, now with a **team auto-roster workflow**.

## What it does
- Single-player prediction mode.
- Team mode: enter only `LAL`, `OKC`, `BOS`, etc.
- Auto-fetches roster via `CommonTeamRoster`.
- Attempts next-opponent detection and home/away context.
- Computes model prediction + opponent averages + blended prediction.
- Ranks top reliable and top raw predictions for PTS/REB/AST/STL/BLK.

## Run
```bash
pip install -r requirements.txt
python app.py
```

## Team auto-roster mode
1. Select mode `3`.
2. Enter team abbreviation.
3. App pulls roster, finds next opponent (fallback: General Estimate), predicts each player, and prints rankings.

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
