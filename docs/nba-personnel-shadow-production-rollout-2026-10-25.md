# SmartBetSports NBA Personnel + Shadow Production Rollout

Date: 2026-10-25 EDT / 2026-10-26 UTC

## Outcome

The reviewed additive personnel migration and reviewed API/frontend artifacts are live in Production. The first monitored NBA cycle succeeded. It refreshed all 30 teams, stored 518 immutable personnel snapshots, added five new Elo baseline predictions, created 104 isolated research-shadow predictions, and settled the first eligible live NBA prediction.

No production NBA model was promoted, no wager was authorized, NFL V2 was not changed, and the Agent Core was not changed. The optional 30-minute Supabase personnel scheduler was prepared locally but was not deployed or activated.

## Preflight, backup, and migration

- Final point-in-time backup: `artifacts/production-backups/smartbetsports-production-pre-personnel-20261025-200809.dump`
- Size: 4,506,351 bytes
- SHA-256: `65AE302468953CA5917D9EC91033F4181773A706DCF10A0B2E916D2116689701`
- Recovery check: restored into isolated PostgreSQL 17.6 container `smartbets-personnel-restore-20261025`; protected counts and prediction hash matched Production.
- Migration: `20261025160000_nba_personnel_freshness`
- Reviewed migration SHA-256: `54060c8fc7453f6fb5dde53dc6dfb178ed7cafb38de951a40a2618f6002fb893`
- Ledger result: inserted as version `20261025160000`, name `nba_personnel_freshness`; no unapplied migration was falsely marked executed.
- Migration result: additive and transactional. It created the two personnel/freshness tables, immutable update/delete triggers, RLS restrictions, and the research-candidate registry row. No existing records were deleted or rewritten.
- Access result: `anon`, `authenticated`, and `service_role` have no direct table read access; the server database role retains operational access.

Database rollback remains independent from Vercel rollback: the additive tables and ledger row are retained to preserve evidence, while shadow collection can be disabled and application aliases can be rolled back independently.

## Production deployments

- Customer URL: <https://smartbetsports.com>
- API URL: <https://smartbetsports-api.vercel.app>
- API deployment: `dpl_96VzWW13c6UNnSHUcos2gW6Gd6dP` (`Ready`)
- Frontend deployment: `dpl_8LXSTaxPh8eKAsf2vtg2ivNKhY62` (`Ready`)
- `NBA_SHADOW_ENABLED`: enabled in Vercel Production.
- Existing Elo baseline remains the customer-facing NBA read model.
- All three NBA registry rows remain `RESEARCH_ONLY` and `production_eligible=0`.
- Database evidence shows `wagering_authorized=0` for all baseline and shadow predictions.

The first API promotion attempt exposed a transient Vercel Python dependency-extraction failure (`/tmp` no-space condition). Both aliases were immediately rolled back and shadow was disabled. The exact artifact was then rebuilt with the warmed build cache, passed six consecutive protected readiness checks, and only that verified deployment was promoted. This remains a packaging-risk follow-up because the function still relies on Vercel's optimized runtime dependency extraction.

## Monitored NBA cycle

- Trigger: existing Vercel Cron control, manually invoked through `vercel crons run`.
- Run ID: `nba-auto-6687c7a08d7a41768caa286c37a35dde`
- Started: `2026-10-26T00:20:08Z`
- Completed: `2026-10-26T00:21:34Z`
- Status: `SUCCEEDED`; no error code.
- Schedule: 213 games fetched, 11 changed, 104 finals observed.
- Elo baseline: 5 created, 99 already present; total is now 110.
- Shadow candidate: 104 created, 104 paired with baseline games, zero reused snapshot IDs.
- Personnel: 30/30 teams, 518 athletes/snapshots, provider `espn`, zero missing provider/retrieval timestamps.
- Freshness: 30 roster sources and 30 availability sources are `FRESH`; team index is `FRESH` with 30 teams.

Integrity checks:

- Duplicate prediction groups: 0.
- Post-tip predictions: 0 for baseline; 0 for shadow.
- Shadow rows: 104/104 explicitly `shadow_only`; 0 wager-authorized.
- Pregame baseline preservation: the original 105-row hash remains `f88d46518868cb97f9de8d551739e432`, exactly matching the preflight backup.
- Separate timestamps: all 104 paired shadow predictions have a timestamp distinct from their paired baseline; none share a snapshot ID.

## First verified live NBA settlement

- Game: Lakers at Jazz (`espn-401909861`)
- Final: LAL 132, UTAH 123
- Frozen baseline prediction: UTAH, generated `2026-10-25T09:06:54Z`
- Tip time: `2026-10-25T21:00:00Z`
- Settlement: `LOSS`, settled `2026-10-26T00:20:47Z`
- Current baseline performance: 1 settled, 0 wins, 1 loss, 0 pushes, 0 invalid predictions included; accuracy is therefore 0% on the one-game live sample.

This is evidence that final-score ingestion, immutable pregame lookup, settlement persistence, and baseline performance aggregation are operating. It is not evidence of model quality because the live sample is one decision.

## NFL and protected-data regression

- NFL V2 is still the sole production winner model: `nfl_game_baseline_v2`.
- Counts remained 230 game predictions, 230 history rows, and 117 results.
- Users remained 6; Stripe webhook rows remained 0.
- No Agent Core file was edited during this rollout.

The latest unchanged NFL lifecycle completed, but its audit reports one critical item: `Multi-game weekly uniqueness`. This is a pre-existing audit false-positive, not an NFL V2 regression. The pre-rollout restored backup already contained one legacy `ELITE_80` benchmark and one current `CORRELATED_RESEARCH` benchmark for Week 7, while the audit currently counts both strategies against a single-strategy uniqueness rule. The restored backup already showed the same failure before this rollout. Two real data-quality warnings remain: 0/14 canonical Week 7 predictions have legitimate pregame entry prices, and one upcoming game has stale/missing market capture. No NFL data or model was changed to suppress these findings.

## Frontend, API, and authentication

- Production homepage rendered successfully after promotion.
- Protected NBA and Command Center API calls returned normalized `401 AUTHENTICATION_REQUIRED` without a session.
- Browser navigation to the NBA board redirected to login, confirming the customer access boundary.
- The previous owner session had expired. The Command Center owner-login screen rendered, but an authenticated owner-session smoke test could not be completed without bypassing authentication, which was not attempted.
- Unit/integration coverage validates baseline-only customer history/performance and internal shadow comparison paths; live database checks validate the backing Command Center freshness state.

## Scheduler state

- Vercel registered `/api/cron/nba-active-season` at `17 10 * * *`.
- Next expected execution: 2026-10-26 at 10:17 UTC. On Vercel Hobby, daily jobs have hourly timing precision, so the practical window is approximately 10:17-11:16 UTC.
- `/api/cron/social-daily` remains registered at 17:30 UTC.
- Production Supabase currently has `pg_cron 1.6.4`, `pg_net 0.20.4`, and `supabase_vault 0.3.1`.
- Existing Supabase jobs remain unchanged. No NBA personnel job exists, and no dedicated NBA personnel Vault secret exists.

## Prepared 30-minute personnel refresh (not active)

Prepared but not deployed:

- A dedicated `POST /api/cron/nba-personnel` endpoint.
- Separate `NBA_PERSONNEL_REFRESH_SECRET` authentication.
- Fail-closed `NBA_PERSONNEL_REFRESH_ENABLED` flag.
- An inactive SQL template at `supabase/proposals/nba_personnel_30m_cron.sql` using Vault + `pg_net` on `*/30 * * * *`.
- Three focused endpoint/authentication tests passed as part of the full suite.

Expected load is 48 invocations/day, about 1,440 per 30-day month. This is far below Vercel Hobby's included one million monthly function invocations, but each refresh consumes CPU/memory and the plan pauses rather than billing on-demand if included limits are exceeded. The observed 30-team personnel section completed in roughly 42 seconds, under the documented configurable 60-second Hobby function limit. Supabase Cron uses existing project compute; the incremental subscription price is expected to be $0, but database load and function CPU/memory must be monitored. Supabase recommends no more than eight concurrent jobs and no job longer than ten minutes.

Separate activation approval must explicitly authorize all of the following:

1. Deploy the prepared personnel-only endpoint.
2. Create the Production Vercel enable flag and dedicated secret.
3. Create the matching Vault secret.
4. Execute the commented `cron.schedule` statement.
5. Monitor at least two 30-minute cycles for overlap, duration, freshness, and failure history.
6. Stop rather than upgrade or incur charges if free-tier limits are insufficient.

## Validation

- Application Python suite: 896 passed, 1 skipped, 1 deprecation warning.
- Frontend suite: 44 passed.
- Targeted personnel-cron tests: 3 passed.
- Next.js production build: passed before promotion.
- Repository-root pytest collection still cannot import the separately packaged `smartbets_agent_core` from the application virtualenv. The application suite was run directly; Agent Core was not modified.

## Remaining issues

1. Authenticated owner-session browser smoke test remains pending because the available owner session expired.
2. The API's large Python runtime dependency bundle had one transient no-space cold-start failure; dependency splitting/pinning should be reviewed before the next broad API rollout.
3. The NFL audit's cross-strategy multi-game uniqueness rule produces a false critical status. An audit-only patch can correct the rule without changing NFL V2, but it was not included in this reviewed production artifact.
4. NFL Week 7 market capture has real coverage/freshness warnings.
5. The 30-minute Supabase personnel scheduler is intentionally inactive pending the separate approval listed above.
6. Vercel documents Hobby for personal/non-commercial use; plan eligibility should be reviewed before treating the subscription business as commercially launched. No plan change or paid charge was made.

References: [Supabase Cron](https://supabase.com/docs/guides/cron), [Supabase Vault](https://supabase.com/docs/guides/database/vault), [Supabase pg_net](https://supabase.com/docs/guides/database/extensions/pg_net), [Vercel Hobby plan](https://vercel.com/docs/plans/hobby), [Vercel Cron limits](https://vercel.com/docs/cron-jobs/usage-and-pricing), [Vercel Functions usage](https://vercel.com/docs/functions/usage-and-pricing).
