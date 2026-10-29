# NFL production preparation update

Preparation cutoff: **2026-10-29 05:02 UTC / 2026-10-29 01:02 EDT**.

## Outcome

The implementation is production-shaped and has a disabled, unpromoted Vercel
Production-target build. Production activation remains blocked by the database
management boundary described below. No production Cron job was installed, no
production ingestion cycle ran, and no production prediction was created or
changed.

## Completed hardening

- Disabled mode now returns before database initialization or provider access.
- Staging and production use explicit environment names, independent Cron job
  names, and independent Supabase Vault secret names.
- The scheduler installer has a second, default-closed gate and rejects a
  deployment-environment/database-role mismatch.
- A protected read-only readiness endpoint reports sanitized database identity,
  role scope, migrations, scheduler state, and activation blockers.
- The Cron SQL is environment-neutral and grants only the bounded operations
  required by `smartbets_staging_app` or `smartbets_production_app`.
- Superseded staging policies were removed, and the forecast/run foreign key now
  has a covering index.
- Staging Cron uses the durable alias
  `https://smartbetsports-api-staging.vercel.app`, so Preview deployment URLs no
  longer have to be embedded in the schedule.
- NFL V2 model logic and identity were not changed.

## Staging evidence

| Check | Result |
|---|---|
| Supabase project | `reyseyjksdfgcqyrpjtw`, healthy |
| Vercel Preview | `dpl_BDZjwBa1ddU2Lnbq1ca2F3Z86bAi`, Ready, stable staging alias assigned |
| Readiness | HTTP 200, `ready=true`, dedicated role confirmed, installer closed |
| Scheduler | `smartbets-nfl-data-refresh-30m-staging`, job 8, active, `*/30 * * * *` |
| Controlled refresh | `SUCCEEDED`; 0 history inserts, 28 duplicates, 0 conflicts, 14 unchanged forecasts, 0 official predictions changed |
| Completed games | 107 games / 214 team-history rows |
| Week 8 | 14 forecast snapshots and 14 official frozen predictions |
| Pipeline failures | 0 |
| Production Cron jobs | 0 |
| Install gate test | HTTP 409 while the final Preview has the installer disabled |
| First stable-alias scheduled run | Cron succeeded at 2026-10-29 05:00:00 UTC / 01:00:00 EDT; durable pipeline success at 05:00:10 UTC / 01:00:10 EDT with 0 inserts, 28 duplicates, 0 conflicts, 14 unchanged forecasts, and 0 official prediction changes |

The next scheduled execution is 2026-10-29 05:30:00 UTC / 01:30:00 EDT.

## Production-target evidence

The final inspected build is Vercel deployment
`dpl_6dLwzq7vky84La6GaBqDbmkRVLKZ` at
`https://smartbetsports-7kxg0808l-chrishamie141s-projects.vercel.app`, built
from commit `37db0bf2a47525a10e00c5cca7a5d7c159fa6859`.
It was built with `--prod --skip-domain`; `productionUrl` is null and the
deployment has no aliases. The live production domains still point to the prior
deployment.

The protected readiness response verified:

- `environment=production`
- `refreshEnabled=false`
- `installerEnabled=false`
- no production Cron job
- Supabase project ref `jcjlztdmxdzrbnbrnmmn`
- database role `postgres` (not acceptable for the worker)
- NFL pipeline migrations absent

Production has a distinct refresh bearer and explicit disabled configuration.
The bearer was not printed or persisted in an artifact.

## Remaining blocker

The connected Supabase management account cannot access project
`jcjlztdmxdzrbnbrnmmn`; the API returns a permission error. Consequently a
restorable backup, advisors, migration history, and a dedicated
`smartbets_production_app` credential cannot be verified or provisioned through
the approved management plane. The worker must not be activated using the broad
`postgres` credential, and the application credential must not be used as a
backdoor around this management boundary.

Required resolution:

1. Grant the connected Supabase account project-level access to
   `jcjlztdmxdzrbnbrnmmn`, or reconnect the correct Supabase organization.
2. Verify/obtain a restorable backup and run pre-migration advisors.
3. Create `smartbets_production_app` with a unique credential and only the
   reviewed grants.
4. Apply the additive migrations and rerun advisors.
5. Point the NFL refresh worker at the scoped credential and verify readiness
   reports no blockers while both activation switches remain false.
6. Request a separate activation approval before installing the production Cron
   job or setting `NFL_DATA_REFRESH_ENABLED=true`.

## Verification

- NFL pipeline/product/server plus NBA availability/model/replay/shadow tests:
  **90 passed**.
- Frontend tests: **47 passed**.
- Frontend production build: **passed**.
- Supabase security advisor: no new warning attributable to this pipeline.
- Supabase performance advisor: the pipeline's missing foreign-key index and
  duplicate-policy warnings are resolved; remaining items predate this change.

## Recommendation

**NOT READY FOR PRODUCTION ACTIVATION.** Application and staging preparation are
complete, but production database access must be corrected before the scoped
role, backup, and migrations can be safely completed.
