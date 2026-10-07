# NFL production audit and model policy

The production lifecycle runs `audit_week` after ingestion, settlement, and
benchmark grading. The audit canonicalizes per-user copies into one prediction
per season/type/week/game. A system-owned snapshot is preferred, with conflicts
reported explicitly rather than counted as additional predictions.

Weekly audit output includes winner accuracy, fixed probability buckets, Brier
score, market coverage, stale-capture warnings, flat-unit ROI, and closing-line
value. ROI requires an attributed sportsbook price whose provider timestamp and
retrieval timestamp are both strictly before the operational kickoff. CLV also
requires a verified final pre-kickoff closing observation. Missing or postgame
prices are never backfilled.

Run an audit with:

```powershell
python smartbets.py audit --league NFL --season 2026 --season-type regular --week 5 --json
```

The owner-only season rollup is available at
`/api/internal/operations/nfl-production/weekly-audits/{season}/{season_type}`.

Model governance is enforced in `backend/app/services/nfl_model_policy.py`:

- NFL winner V2 is the production champion.
- NFL winner V3 is challenger/research only.
- Player Props V4 is research only and blocked from promotion until forward
  testing demonstrates positive ROI with adequate calibration and sample size.
- Parlays are experimental. Individual leg probabilities may be displayed, but
  a joint ticket probability is unavailable until a validated correlation-aware
  estimator exists. Independent probability multiplication is not a publishable
  SmartBet probability.
