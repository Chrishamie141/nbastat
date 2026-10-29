# NFL cloud ingestion production-readiness verification

Verification cutoff: **2026-10-29 02:12 UTC / 2026-10-28 22:12 EDT**.

## Executive result

Staging is healthy and cloud-independent. The current Supabase Cron job is
enabled and the final Vercel Preview deployment continued running unattended
every 30 minutes. Production is **not ready to activate** because the
production database target and role policy have not been verified, the new
production variables and scheduler do not exist, the database migrations are
staging-specific, and the implementation has not been committed and reviewed.

## Required status report

| Item | Verified result |
|---|---|
| Staging pipeline | Healthy; Supabase project `reyseyjksdfgcqyrpjtw` is `ACTIVE_HEALTHY`; Vercel deployment `dpl_33WApy4gv5wgSvdGcWqe5G97qzKu` is Ready |
| Scheduler | Job 6, `smartbets-nfl-data-refresh-30m-staging`, active, `*/30 * * * *` |
| Last scheduled execution | Cron queued at 2026-10-29 02:00:00 UTC / 2026-10-28 22:00:00 EDT; durable pipeline success at 02:00:09 UTC / 22:00:09 EDT |
| Next scheduled execution | 2026-10-29 02:30:00 UTC / 2026-10-28 22:30:00 EDT |
| Latest ingested game | Dallas 38 at Philadelphia 21, ESPN game `401873009`, Week 7; kickoff 2026-10-27 00:15 UTC / 2026-10-26 20:15 EDT |
| Data cutoff | 2026-10-27 06:15 UTC / 02:15 EDT; this is the documented conservative kickoff-plus-six-hours boundary, not a provider final-status timestamp |
| Completed-game coverage | 107 games / 214 team rows. Exact ID comparison against ESPN Weeks 1-8 found 107 official finals, zero missing, and zero unexpected database games |
| Week 8 | Current: all 14 provider-scheduled games have forecasts, with matching kickoff times and a history cutoff equal to the canonical latest cutoff; all use 758 eligible history rows and `nfl_game_baseline_v2` |
| Staleness | No stale upcoming forecasts. The content was first frozen at 2026-10-28 18:52 UTC and was revalidated unchanged on each scheduled run through 02:00 UTC |
| Failures | 21 pipeline runs succeeded, zero failed, zero source conflicts. Job 6 produced 14 consecutive unattended scheduled successes from 19:30 through 02:00 UTC; all 14 Vercel requests returned HTTP 200 |
| Cloud independence | Confirmed. Supabase Cron/Vault/pg_net invokes Vercel Preview, which reads ESPN and writes durable PostgreSQL records. No local process, local server, manual command, or connected Codex session is part of ongoing execution |

## Reliability and safety verification

- Every-30-minute execution covers Thursday, Sunday, Monday, late games, and
  schedule changes without day-specific assumptions.
- Only provider-final games with complete identities, teams, kickoff times,
  and non-negative scores are accepted. Missing expected finals fail closed.
- The active and prior week are rechecked every run; missing historical weeks
  are backfilled after an outage or missed schedule window.
- History and forecast evidence are append-only. A database trigger now also
  protects system prediction content and blocks deletion; after kickoff all
  system-prediction fields are immutable.
- A PostgreSQL session advisory lock now spans the entire execution. A live
  two-request concurrency test returned one `SUCCEEDED` result and one
  `ALREADY_RUNNING` result with no second provider contact.
- Sequential duplicate runs remained no-ops: 28 existing team rows and 14
  unchanged forecasts were recognized without data mutation.
- Week 8 has 14 official system predictions and 14 forecast snapshots; none
  were generated at or after kickoff.
- NBA remained unchanged at 219 prediction snapshots and its existing staging
  personnel Cron job stayed active. No Agent Core project or service was
  modified.

## Validation results

- NFL current-season/product/server tests: **44 passed**.
- NBA regression selection: **49 passed**.
- Frontend tests: **47 passed**.
- Frontend production build: **passed**.
- Full application test directory: **907 passed, 1 skipped, 1 unrelated
  existing failure**. The failure is the stale test that still expects the
  removed `Fantasy Football` option.
- Supabase security advisor found no new security warning for this pipeline;
  it continues to report the pre-existing informational server-only RLS items.
  The performance advisor reports an informational missing index on the small
  forecast-to-run foreign key, not a current correctness issue.

## Production blockers and risks

1. The Supabase project named `SmartBetSports` is currently `INACTIVE`, and its
   identity has not been safely proven to be the Vercel Production
   `POSTGRES_URL` target. Production database mapping must be verified without
   exposing credentials.
2. `NFL_DATA_REFRESH_ENABLED` and `NFL_DATA_REFRESH_SECRET` exist only in the
   Vercel Preview environment. The Production deployment does not contain the
   new endpoint or a production scheduler.
3. The current SQL boundary names the staging role and staging Cron job. A
   reviewed production-role migration and a distinct production job name are
   required; staging credentials and Vault entries must not be reused.
4. The implementation is still an uncommitted working-tree deployment. `main`
   remains at `e9d6916`; a reviewed commit/PR is required before production.
5. Failures are durable and visible through database state, protected status,
   Vercel logs, and the board freshness warning, but there is no proactive
   notification escalation for repeated failures. Add an operational alert
   before unattended production reliance.
6. A real scheduled post-game transition has not yet occurred on this final
   Preview deployment because the first Week 8 game has not kicked off. The
   catch-up/refresh path is covered by tests and the initial backfill, but a
   prospective Thursday-final shadow observation would further reduce risk.

## Exact production activation sequence

These actions require explicit production approval and were **not performed**:

1. Commit and review the code, migrations, tests, and rollback documentation;
   deploy that immutable revision to Preview again.
2. Identify and health-check the actual Production PostgreSQL target, restore
   the intended production Supabase project if it is the target, and take a
   restorable backup/snapshot.
3. Create or verify a dedicated least-privilege production application role.
   Produce production-specific migrations for tables, policies, prediction
   immutability, Vault access, and Cron status; remove all staging role/job
   identifiers and rehearse on a production-shaped clone.
4. Reconcile existing production system-prediction metadata before enabling
   the immutability trigger, then record row counts and hashes for NFL
   predictions, grading records, NBA tables, and existing Cron jobs.
5. Add a unique Production `NFL_DATA_REFRESH_SECRET` and set
   `NFL_DATA_REFRESH_ENABLED=false`; deploy the reviewed API revision without
   installing or enabling the new scheduler.
6. Verify the disabled endpoint performs no provider contact, validate the
   protected status endpoint, database permissions, rollback deployment, and
   alert destination.
7. After a separate explicit activation approval, set the production flag to
   true and install one distinct
   `smartbets-nfl-data-refresh-30m-production` job targeting the stable
   production API origin.
8. Run one controlled production cycle; verify expected inserts/no-ops,
   unchanged NFL V2 identity, unchanged historical hashes and grading counts,
   unchanged NBA/Agent Core state, and no duplicate jobs.
9. Observe at least one Thursday, Sunday, or Monday final-to-refresh cycle and
   confirm the alert/recovery path. Roll back by disabling the flag,
   unscheduling only the new production job, and reverting the deployment;
   retain append-only evidence.

## Recommendation

**NOT READY FOR PRODUCTION.** Staging is operational and independently healthy,
but the production target, credentials, role policy, reviewed release artifact,
scheduler, and alerting gate are not yet in place.
