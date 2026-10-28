# NBA Personnel and Shadow Research — Staging Validation

Date: 2026-10-25
Scope: isolated Supabase staging and Vercel Preview only
Production changed: no

## Outcome

The additive personnel migration, NBA roster/availability refresh, and research
shadow candidate are deployed and verified in the existing isolated staging
environment. The established Elo baseline remains the customer-facing NBA read
model. `nfl_game_baseline_v2` and the Agent Core were not changed.

Final Preview deployments:

- API: `https://smartbetsports-hfo6waims-chrishamie141s-projects.vercel.app`
- Frontend: `https://smartbetsports-gbz53g040-chrishamie141s-projects.vercel.app`

Neither deployment has a Production alias. The frontend Preview
`BACKEND_API_URL` points to the API Preview above. Production variables and
deployments were not modified.

## Database migration

Applied only to the staging database as `smartbets_staging_app`:

- Migration: `20261025160000_nba_personnel_freshness`
- Source SHA-256: `54060c8fc7453f6fb5dde53dc6dfb178ed7cafb38de951a40a2618f6002fb893`
- Application ledger: recorded in the same transaction with the source hash
- New tables: `nba_player_availability_snapshots`, `nba_data_source_status`
- Access: RLS enabled; no `anon`, `authenticated`, or `service_role` table privileges
- Evidence: availability snapshots reject update and delete operations
- Governance: candidate registered as `RESEARCH_ONLY`, production-ineligible

Pre/post protected counts were unchanged by the migration: one user, zero
billing webhook events, 26 NFL history rows, 208 NBA games, and 114 NBA baseline
predictions. No proposed-constraint violations were found before application.

## Live staging validation

Two authenticated automation cycles were executed against staging.

- ESPN personnel refresh: 30/30 teams, 518 players per cycle, zero provider errors
- Immutable personnel evidence: 1,036 snapshots across two retrieval batches
- Freshness status: 30 roster `FRESH`, 30 availability `FRESH`, one team feed `FRESH`
- Provider and retrieval timestamps: present on every snapshot
- Baseline predictions: 114, evidence hash unchanged
- Shadow predictions: 105, paired to the same 105 baseline games
- Independent timestamps: 105/105 pairs
- Duplicate prediction groups: zero
- Post-tip or feature-cutoff violations: zero
- Settlements: nine baseline predictions (six wins, three losses)
- Customer performance page: 66.7% NBA accuracy from nine graded predictions
- Immutability probe: database rejected an attempted no-op prediction update

The repeat cycle fetched 208 games and reported zero game changes, zero new
baseline predictions, 105 baseline predictions already present, zero new shadow
predictions, and 105 shadow predictions already present. It reported all nine
settlements already settled. This confirms live idempotence for the current
slate.

The final staging record counts are one user, zero billing webhook events, 26
NFL history rows, 219 NBA prediction rows (114 baseline plus 105 shadow), and
nine settlements. The disposable browser-smoke subscriber was deleted after
testing.

## Frontend and Command Center

An authenticated disposable staging subscriber verified:

- combined NBA/NFL dashboard data and league filter;
- current NBA final scores;
- NFL V2 game board data;
- immutable NBA history and research-only labels;
- nine graded NBA results and 66.7% performance reporting.

The disposable non-owner was redirected away from `/command-center`, confirming
the owner boundary. No authenticated owner browser session was available, so an
owner-only visual smoke remains outstanding. The Command Center backend and UI
paths are covered by regression tests, and its source data was verified directly:
freshness states, source failures, pair counts, disagreements, per-model metrics,
and scheduler health remain available internally.

Customer read APIs now explicitly exclude shadow rows. Shadow evidence remains
available only to explicit internal comparison paths, preventing a research
candidate from appearing as the established customer-facing model.

## Independent historical evaluation

The reproduction does not import the original evaluator. It independently
rebuilds time-ordered features and verifies the source dataset hash.

- Provider dataset: 3,693 verified ESPN regular-season finals
- Seasons: 2023-24 train, 2024-25 validation, 2025-26 untouched test
- Games per season: 1,231
- Duplicate game IDs: zero
- Missing scores: zero
- Ties: zero
- Feature-cutoff violations: zero
- Split overlap: zero
- Strict chronological ordering: confirmed
- Hyperparameter selection: validation Brier score only (`C=0.1`)
- Independent/reference probability delta: exactly zero for both models

Held-out 2025-26 results:

| Metric | Elo baseline | Form/rest candidate | Candidate change |
| --- | ---: | ---: | ---: |
| Accuracy | 66.13% | 68.56% | +2.44 points |
| Brier score | 0.21218 | 0.20461 | -0.00757 |
| Log loss | 0.61264 | 0.59573 | -0.01691 |
| ECE | 0.05747 | 0.02300 | -0.03447 |

A 4,000-sample paired calendar-week cluster bootstrap found candidate
improvements of +0.02470 accuracy (95% CI -0.00076 to 0.05114), +0.00765 Brier
skill (0.00220 to 0.01335), and +0.01707 log-loss skill (0.00468 to 0.02978).
Probability metrics improved reliably in this sample, but the accuracy interval
narrowly crosses zero. This is evidence for prospective research, not promotion.

Reproducible artifacts are under `artifacts/nba-model-quality-2026-10-25/`,
including the executed independent notebook, JSON results, uncertainty charts,
and the frozen research artifact. The embedded canonical artifact hash is
`399bd1e39c003c06393a6d6ec04dfd841b5b89a379c3ce1c127647740b25c307`.

## Data limitations and closer-to-tip strategy

ESPN provides useful roster and reported-injury evidence, but
`NO_INJURY_REPORTED` is not the same as confirmed active. The candidate does not
consume personnel fields yet; adding them before chronological feature-availability
and leakage validation would overstate model readiness. Bulk player-stat refresh
also remains outside this personnel cycle; existing player-stat support is
on-demand with explicit health reporting.

Vercel Hobby cron is production-only and daily, so it cannot provide reliable
near-tip Preview refresh. The staging Supabase project currently has Vault but
does not have `pg_cron` or `pg_net` installed. The lowest-cost next design is:

1. add a narrow authenticated personnel-only endpoint that refreshes only teams
   playing within the next six hours;
2. enable Supabase Cron plus `pg_net` and keep the endpoint secret in Vault;
3. run every 30 minutes during the NBA game window, with a final pass about 30
   minutes before tip;
4. persist each attempt and alert on stale/error states in the existing Command
   Center.

This reuses existing infrastructure and requires no paid provider. `pg_net` is
currently documented as beta, so retries, timeouts, and a daily Vercel fallback
must remain. GitHub Actions is a fallback, but scheduled runs can be delayed and
consume Actions minutes, making it less reliable for near-tip freshness.

## Regression evidence

- Backend full suite before the final read-boundary hardening: 894 passed, one skipped
- Final focused NBA suite after hardening: 47 passed
- Frontend: 44 passed
- Next.js optimized build: passed
- Final complete backend suite after read-boundary hardening: 894 passed, one skipped

Only the existing Starlette/AnyIO deprecation warning remains. No NFL model file
or Agent Core file was modified by this phase.

## Remaining blockers and exact production approval

Production remains blocked pending all of the following explicit approvals:

1. Approve a production backup/read-only preflight and application of only
   `20261025160000_nba_personnel_freshness.sql` after zero constraint violations
   and a matching source hash are confirmed.
2. Approve deployment of the reviewed API and frontend artifacts to Production.
3. Separately approve `NBA_SHADOW_ENABLED=true` in Production for internal
   prospective shadow collection. This does not promote the candidate, change
   the customer read model, or authorize wagering.
4. Provide or perform one authenticated owner-session smoke of the Production
   Command Center after deployment.
5. If closer-to-tip refresh is desired, separately approve the personnel-only
   endpoint, Supabase `pg_cron`/`pg_net` extensions, Vault secret, and schedule.
   No paid service is proposed.

No approval to promote the candidate should be issued yet. It still requires a
meaningful prospective paired sample, market benchmarking, calibration review,
and manual model-governance approval. NFL V2 and the Agent Core remain outside
that decision.
