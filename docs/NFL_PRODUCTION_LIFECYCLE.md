# NFL production lifecycle

SmartBetSports treats the original pregame prediction and every internal SGP
benchmark as immutable evidence. Settlement writes only to dedicated result
fields after ESPN reports a final. Missing scores or player statistics remain
pending and are retried; they are never converted to losses.

## Lifecycle

1. The weekly board stores the pregame prediction in `nfl_game_predictions`.
2. The production worker records its hash in `nfl_prediction_history`.
3. Before kickoff, each eligible game receives one SAFE, BALANCED, and
   AGGRESSIVE benchmark, or an explicit `NO_BET` when verified markets are
   insufficient.
4. At kickoff all source artifacts are locked by the timestamp boundary.
5. ESPN finals and box scores are stored in `nfl_game_result_snapshots`.
6. Predictions, customer parlays with game identity, benchmark legs, and
   benchmark tickets settle idempotently.
7. The production audit verifies the complete chain and stores its report.

Customer parlays and internal benchmark tickets are separate datasets. A
customer ticket never changes model-performance aggregates.

## Audit

```powershell
python smartbets.py audit --league NFL --season 2026 --season-type regular --week 1
```

The command exits non-zero when a critical integrity check fails. The same
audit is available to owners from **Command Center → NFL production health →
Run Production Audit**.

## Automation

The authenticated `/api/cron/nfl-active-season` job keeps its existing market
capture behavior and advances one bounded benchmark/lifecycle unit per run.
Repeated runs are safe. Provider failures are logged using error classes only;
responses and logs never contain credentials.

The schema migration is
`supabase/migrations/20260910050500_add_nfl_production_lifecycle.sql`. These
tables are server-only: RLS is enabled and access is revoked from `anon` and
`authenticated` roles.

## Historical limitations

Old customer parlays that predate persisted `game_id` values cannot be graded
reliably. They remain pending. Do not infer a game from free-form notes or
recreate their markets after kickoff.
