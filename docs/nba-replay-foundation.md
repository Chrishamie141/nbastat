# NBA replay and production-data foundation

The NBA and NFL prediction systems are intentionally separate.

- NFL production remains on its independently versioned V2 winner model.
- NBA research starts with `nba-player-stat-v1` in
  `backend/app/services/nba/model.py`.
- An NBA model version must begin with `nba-`; the NBA snapshot and replay
  services reject other model identities.
- No NBA module imports `nfl_predictor.py`, NFL V2, or the NFL registry.

## Season identity

NBA seasons use the canonical provider code `YYYY-YY`. The current season is
resolved centrally when each workflow runs, so October 2026 resolves to `2026-27` rather
than the previously hardcoded `2025-26`.

## Durable evidence

Apply `supabase/migrations/20261007140555_nba_replay_foundation.sql` before
enabling cloud automation. It adds server-only tables for normalized games,
immutable predictions, append-only pregame markets, settlements, replay runs,
and an NBA-only model registry. Database triggers reject evidence at or after
kickoff. RLS is enabled and browser roles have no direct privileges. Apply the
additive `20261007173500_extend_nba_replay_governance.sql` migration after the
foundation migration.

## Schedule and settlement automation

`backend.app.services.nba.schedule.fetch_schedule` queries ESPN in bounded date
batches and deduplicates by provider event ID. The protected route is:

```text
POST /api/cron/nba-active-season
Authorization: Bearer $NBA_AUTOMATION_SECRET
```

Safe defaults keep it disabled until the migration and scheduler are ready:

```env
NBA_AUTOMATION_ENABLED=false
NBA_AUTOMATION_SECRET=
# Optional override; blank resolves from the current date.
NBA_ACTIVE_SEASON=
```

Winner, spread, and total predictions settle when verified finals exist. Player
props remain `UNRESOLVED` until verified box-score statistics exist. Equality
to a line is a `PUSH`; missing data is never a loss. Settlements use `WIN`,
`LOSS`, `PUSH`, `VOID`, and `UNRESOLVED` independently of immutable predictions.

## Historical replay

An offline dataset contains `games.json`, `team_history.json`, `outcomes.json`,
and optionally `frozen_predictions.json`. Run it with:

```bash
python smartbets.py nba-replay --dataset-dir backtesting/data/snapshots/nba/2025-26 --season 2025-26 --model-version nba-player-stat-v1 --output backtesting/results/nba_2025_26_replay.json
```

The replay never calls live providers, never reconstructs a missing prediction,
and rejects predictions generated at or after kickoff. An optional
`markets.json` is joined only when provider, sportsbook, line, selection,
timestamp, and pre-tipoff validation all match. Reports include accuracy,
Brier score, log loss, calibration buckets, market coverage, flat-unit ROI,
units, pushes/unresolved counts, model/market/time-period segments, and missing
data. Prediction quality and betting profitability are reported separately.

## Early-season features

`cold_start.build_cold_start_features` combines up to 20 prior-season games
with completed current-season games. Current-season weight grows by 0.1 per
game and is capped at 0.8. Every row must precede the target kickoff.

Player and team cold-start builders retain prior/current-season provenance,
strict cutoffs, stable player IDs, and observed team changes. This is
infrastructure, not a model promotion.

The registry intentionally has no NBA production champion. Replay evidence is
recorded against `RESEARCH_ONLY` models, and promotion remains blocked pending
adequate chronological sample size, calibration, log loss/Brier review, and
positive forward ROI from legitimate prices plus manual review.

**Current readiness:** NBA infrastructure is production-capable, but the
prediction model is not yet validated for production.
