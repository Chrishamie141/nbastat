# NFL production preparation update

Preparation cutoff: **2026-10-29 11:46 UTC / 2026-10-29 07:46 EDT**.

## Outcome

The NFL current-season cloud pipeline is ready for a controlled production
activation. The production database boundary, migrations, Vercel credentials,
and protected readiness path have been installed and verified while production
refresh remains disabled. No production Cron job was installed, no production
ingestion cycle ran, no live domain was promoted, and no production prediction
or grading record was created or changed.

NFL V2 model logic and identity were not changed.

## Production readiness evidence

| Check | Result |
|---|---|
| Supabase project | `jcjlztdmxdzrbnbrnmmn` (`supabase-aquamarine-kettle`) |
| Worker database role | `smartbets_production_app`; login only, no superuser, database creation, role creation, replication, or inheritance |
| Direct Cron/Vault access | Denied; privileged operations are reachable only through the reviewed security-definer boundary |
| Prediction access | Select/insert only; no update/delete; inserted pipeline predictions must have `user_id=0` |
| Migrations | Eight reviewed additive NFL migrations installed and recorded in `supabase_migrations.schema_migrations` |
| Production pipeline data | 0 history rows, 0 run rows, 0 forecast rows (expected before activation) |
| Production Cron | 0 jobs named `smartbets-nfl-data-refresh-30m-production` |
| Activation flags | `NFL_DATA_REFRESH_ENABLED=false`; `NFL_DATA_REFRESH_INSTALL_ENABLED=false` |
| Scoped Vercel secret | `NFL_DATA_REFRESH_DATABASE_URL` exists as a Production Secret |
| Final readiness deployment | `dpl_7v6KvxHuNABuKrQiPG1tjXVHaLeN` |
| Deployment URL | `https://smartbetsports-4vfc50um2-chrishamie141s-projects.vercel.app` |
| Deployment target | Production-target, Ready, unaliased; live production domain unchanged |
| Commit | `1110c56a2ff18ee4c1d01baff45363c9b413103b` |
| Protected readiness | `ready=true`, zero blockers |
| Readiness database identity | PostgreSQL through `aws-0-us-east-1.pooler.supabase.com`; role `smartbets_production_app`; project ref `jcjlztdmxdzrbnbrnmmn` |
| Readiness migration checks | Pipeline migration and environment boundary both installed |
| Readiness scheduler check | `cronJob=null` |

The protected readiness request used the Production bearer only in process
memory. Vercel does not permit Production Secret values to be exported. No
secret value was written to this report or committed to the repository.

## Credential incident and remediation

During a database-connection verification step, the first generated database
credential appeared in browser diagnostic output. It was immediately treated
as compromised. The database password was rotated at Supabase, the stored Vault
secret was replaced, the Vercel Production secret was rotated, and all later
deployments use only the replacement value. The exposed value is revoked and
cannot authenticate.

The NFL refresh bearer was also rotated to a new Production Secret before the
final protected readiness test. It was held only in process memory and was not
written to disk or an artifact.

## Historical-data preservation checks

The following production baselines were captured before the migration and were
rechecked after installation:

| Dataset | Before | After |
|---|---:|---:|
| Official NFL system predictions | 105 | 105 |
| NFL prediction history | 258 | 258 |
| NFL game results | 135 | 135 |
| NBA prediction snapshots | 262 | 262 |
| NBA settlements | 58 | 58 |

The existing production Cron jobs are still exactly:

- `smartbets-nfl-active-season`, job 1, active, `*/5 * * * *`
- `smartbets-social-active-season`, job 3, active, `*/15 * * * *`

No existing NFL, NBA, social, or Agent Core schedule was changed.

## Staging evidence

The current branch was also validated on staging before production preparation:

| Check | Result |
|---|---|
| Supabase project | `reyseyjksdfgcqyrpjtw` |
| Stable Preview alias | `https://smartbetsports-api-staging.vercel.app` |
| Latest validated Preview | `dpl_6Xzw7fPm7Y7keA13Mf9ki14qstFv` |
| Scheduler | `smartbets-nfl-data-refresh-30m-staging`, active, every 30 minutes |
| Confirmed scheduled start | 2026-10-29 11:00:00.193098 UTC / 07:00:00.193098 EDT |
| Durable success | 2026-10-29 11:00:10 UTC / 07:00:10 EDT |
| Scheduled-run result | 0 inserts, 28 duplicates, 0 conflicts, 14 unchanged forecasts, 0 official predictions changed |
| Completed-game history | 107 games / 214 team-history rows |
| Week 8 | 14 snapshots and 14 frozen official predictions |
| Consecutive failures | 0 |

This staging run was initiated by Supabase Cron against the deployed Vercel
alias. No local computer, local server, manually running Python process, or
connected Codex session was involved.

## Validation

- Relevant NFL/NBA backend suite: **123 passed**.
- Full backend suite: **911 passed, 1 skipped, 4 unrelated pre-existing
  failures** (one stale Fantasy test and three Windows subprocess-handle
  failures).
- Frontend tests: **47 passed**.
- Frontend production build: **passed**.
- Final unaliased Production-target build: **Ready**.
- Public application readiness on the Production-target build: **healthy**,
  PostgreSQL connected, all required NFL/NBA tables present.
- Protected NFL pipeline readiness: **ready**, zero blockers, dedicated role
  confirmed, migrations present, production Cron absent, both activation gates
  closed.

## Residual operational risks

- The production Supabase project is on a Free plan and reports no managed
  backups. The migrations were additive and preserved all measured baselines,
  but upgrading to a plan with restorable backups is strongly recommended
  before or immediately after activation.
- The source branch is still represented by draft PR #111. Production activation
  must use the reviewed commit above (or its merged equivalent); the current
  live domain was deliberately not moved during preparation.
- No production ingestion has run yet by design. The first controlled run must
  be observed and compared with the recorded prediction/history baselines.

## Exact activation procedure (requires separate explicit approval)

1. Approve and merge PR #111, or promote the verified commit through the normal
   release workflow.
2. Keep `NFL_DATA_REFRESH_ENABLED=false`; temporarily set
   `NFL_DATA_REFRESH_INSTALL_ENABLED=true`, deploy, and call the protected
   installer once against `https://smartbetsports-api.vercel.app`.
3. Verify the new job is named
   `smartbets-nfl-data-refresh-30m-production`, targets the live HTTPS origin,
   runs every 30 minutes, and stores its bearer in Supabase Vault.
4. Return `NFL_DATA_REFRESH_INSTALL_ENABLED=false` and redeploy.
5. Set `NFL_DATA_REFRESH_ENABLED=true` and deploy only after the activation
   approval is recorded.
6. Observe the first scheduled run. Require a successful durable run record,
   zero conflicts, expected completed-game counts, current upcoming forecasts,
   and zero changes to frozen official predictions.
7. If any gate fails, immediately set refresh back to false and unschedule only
   `smartbets-nfl-data-refresh-30m-production`; do not touch existing jobs.

## Recommendation

**READY FOR PRODUCTION.** All technical readiness blockers are resolved and the
protected Production-target check reports zero blockers. Production remains
safely inactive pending the founder's explicit activation approval.
