# NFL current-season cloud pipeline

## Scope and invariants

This pipeline supplies validated completed-game score history to the existing
`nfl_game_baseline_v2` champion. It does not retrain or alter NFL V2. Existing
rows in `nfl_game_predictions`, `nfl_prediction_history`, results, settlements,
and grades remain the official evaluated evidence and are never updated by this
worker.

Upcoming games have two explicit concepts:

- `nfl_forecast_snapshots`: append-only latest forecasts generated from the
  newest validated history.
- `nfl_game_predictions`: first pregame system prediction, frozen under the
  existing unique constraint and `ON CONFLICT DO NOTHING` policy.

Final and historical screens continue to use only the official evaluated
prediction. Upcoming boards prefer the latest forecast and disclose its
generation time and the separately frozen official prediction.

## Cloud flow

1. Supabase Cron runs the environment-scoped job
   `smartbets-nfl-data-refresh-30m-{staging|production}` every 30 minutes.
2. `pg_net` posts to the protected Vercel Preview endpoint
   `/api/cron/nfl-data-refresh`.
3. The Vercel function verifies the Preview-only bearer, acquires a PostgreSQL
   session advisory lock for the complete execution, and records a durable run.
4. ESPN's free public scoreboard is checked with up to three bounded attempts.
   The first run backfills every missing week; later runs recheck the active and
   prior week, so a provider outage or missed execution catches up naturally.
5. Finals are accepted only with two distinct teams, a game ID, kickoff, final
   status, and two parseable non-negative scores. Each game creates exactly two
   immutable team rows. Identical rows are no-ops; conflicting source evidence
   fails closed.
6. Only after validated ingestion, NFL V2 runs for scheduled, unstarted games
   using history whose availability timestamp is strictly before kickoff.
7. The forecast ledger appends only a new input/output version. Started games
   are rejected and official evaluated predictions are inserted only when no
   prior system record exists. A database trigger prevents changes to frozen
   system prediction content and prevents any deletion of a system prediction;
   after kickoff, every field on the system prediction is immutable.

ESPN does not expose the instant its public scoreboard changed to final. The
pipeline therefore preserves `source_completed_at` as null, records the factual
`first_observed_final_at`, and uses kickoff plus six hours as a conservative,
explicitly derived availability boundary for chronological features.

## Operations

Required environment variables:

- `NFL_DATA_REFRESH_ENABLED=true`
- `NFL_DATA_REFRESH_INSTALL_ENABLED=false` during normal operation; set to
  `true` only for the bounded scheduler installation request, then set it back
  to `false` and redeploy.
- `NFL_DATA_REFRESH_ENVIRONMENT=staging|production` (Vercel Preview and
  Production infer the same values, but an explicit value is recommended).
- `NFL_DATA_REFRESH_SECRET` (secret, 32+ characters)
- `NFL_DATA_REFRESH_BASE_URL` (the environment's reviewed HTTPS Vercel origin)
- `NFL_DATA_REFRESH_DATABASE_URL` (a secret direct-Postgres URL for the
  environment-specific `smartbets_<environment>_app` role; required in
  production so the worker never inherits the broader application credential)

Protected endpoints:

- `POST /api/cron/nfl-data-refresh` — one bounded catch-up/forecast cycle.
- `GET /api/cron/nfl-data-refresh/status` — state, history counts, recent runs,
  and Supabase Cron state.
- `GET /api/cron/nfl-data-refresh/readiness` — sanitized database identity,
  role scope, migration state, and activation blockers; it never writes.
- `POST /api/cron/nfl-data-refresh/install` — calls the narrowly scoped
  security-definer installer; it does not receive general Cron or Vault access
  and returns `409` unless the separate install gate is enabled.

Freshness is current for 90 minutes after the last successful run. The weekly
board warns instead of claiming that a pick is current when that boundary is
missed.

## Recovery and rollback

Provider and function failures remain visible in `nfl_data_pipeline_runs` and
`nfl_data_pipeline_state`; the next 30-minute execution retries and scans for
missing weeks. Duplicate retries are safe.

Staging rollback is non-destructive:

1. Set `NFL_DATA_REFRESH_ENABLED=false` in Preview and redeploy.
2. Unschedule `smartbets-nfl-data-refresh-30m-staging`.
3. Revert the application deployment.
4. Retain history, run, and forecast evidence for audit; do not delete or edit
   it.

## Controlled production rollout

Production activation is deliberately split into preparation and approval:

1. Confirm the Vercel Production database host and `currentRole` with the
   protected readiness endpoint. The role must be
   `smartbets_production_app`; do not operate this worker as `postgres`.
2. Back up the production database and record current NFL/NBA prediction and
   grading counts.
3. Apply the reviewed additive migrations. Confirm there is no production Cron
   job and both production switches remain `false`.
4. Create/rotate a production-only refresh bearer and configure the stable API
   origin. Never reuse the Preview bearer.
5. Build a Vercel Production-target deployment with `--skip-domain`. Verify the
   protected readiness endpoint on that deployment while both switches are
   false. This deployment must not contact ESPN or write to the database.
6. After explicit approval, enable only the install gate, deploy, call the
   installer once, verify the job target and Vault secret name, disable the
   install gate, and deploy again.
7. Enable the refresh worker, deploy, invoke one bounded cycle, and compare
   history, forecasts, official predictions, NBA counts, and grading counts to
   the pre-activation record.
8. Keep rollback available: disable the refresh worker, unschedule only
   `smartbets-nfl-data-refresh-30m-production`, and restore the prior Vercel
   deployment. Preserve all pipeline evidence.

Production remains disabled until the approval step. No staging database
object or Preview secret is copied to production, and the installer rejects an
environment/role mismatch.

## Reproduction

Run the isolated validation suites:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_nfl_current_season_pipeline.py tests/test_nfl_product_service.py tests/test_nfl_server_automation.py
cd frontend
npm test
npm run build
```

Run the read-only chronological comparison:

```powershell
.\.venv\Scripts\python.exe scripts/audit_nfl_current_season_refresh.py
```
