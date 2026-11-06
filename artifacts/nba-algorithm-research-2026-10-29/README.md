# NBA algorithm research artifact

This directory contains the reproducible, research-only NBA game-winner diagnosis completed on 2026-10-29.

## Contents

- `REPORT.md`: narrative findings and recommendation.
- `data/summary.json`: compact reviewed result set.
- `data/historical-model-evaluation.json`: metrics, tuning trials, uncertainty, calibration, and held-out predictions.
- `data/independent-reproduction.json`: separately implemented reproduction of the earlier 68.56% candidate result.
- `data/model-comparison.csv`: held-out model table.
- `data/season-metrics.csv`: validation/test season metrics.
- `data/held-out-predictions.csv`: all 1,231 held-out games with each model probability.
- `data/current-team-ratings.csv`: all 30 current Elo ratings and last completed-game update.
- `data/upcoming-forecast-diagnostics.csv`: all 102 current Elo forecasts and their exact math.
- `data/upcoming-research-candidate-forecasts.csv`: research candidate forecasts on the same cutoff.
- `data/calibration-buckets.csv`: ten-bin calibration evidence.
- `data/current-schedule-snapshot.json`: bounded provider schedule response used for the current diagnosis.
- `data/staging-validation-2026-11-06.json`: controlled cloud staging execution receipt, data-integrity checkpoints, and current selection distribution.

## Reproduce

From the repository root with the project virtual environment:

```powershell
.\.venv\Scripts\python.exe -m backtesting.nba_algorithm_diagnosis `
  --dataset artifacts/nba-model-quality-2026-10-25/nba-history-2023-2026.json `
  --output-dir artifacts/nba-algorithm-research-2026-10-29/data
```

Independently reproduce the earlier candidate artifact:

```powershell
.\.venv\Scripts\python.exe -m backtesting.reproduce_nba_model_quality `
  --dataset artifacts/nba-model-quality-2026-10-25/nba-history-2023-2026.json `
  --reference artifacts/nba-model-quality-2026-10-25/model-comparison.json `
  --report artifacts/nba-algorithm-research-2026-10-29/data/independent-reproduction.json `
  --artifact artifacts/nba-algorithm-research-2026-10-29/data/reproduced-candidate-artifact.json
```

The first command makes read-only provider requests for the bounded 2026-10-01 through 2026-11-12 schedule window and writes local artifacts only. It does not connect to or mutate an application database.

## Governance

- All evaluated candidates remain `RESEARCH_ONLY`.
- Wagering authorization remains false.
- Production Elo serving was not changed.
- NFL V2 was not changed.
- Historical NBA predictions and grading records were not changed.
