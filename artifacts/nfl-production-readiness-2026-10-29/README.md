# NFL cloud ingestion production readiness

Final verification cutoff: **2026-10-29 11:46 UTC / 07:46 EDT**.

## Verdict

**READY FOR PRODUCTION**, subject to the existing explicit activation approval
gate.

The staging workflow is healthy and cloud-independent. Production now has the
reviewed migrations, a dedicated least-privilege database role, scoped Vercel
secrets, and an unaliased Production-target build whose protected readiness
response reports `ready=true` with zero blockers.

Production remains inactive by design:

- `NFL_DATA_REFRESH_ENABLED=false`
- `NFL_DATA_REFRESH_INSTALL_ENABLED=false`
- no `smartbets-nfl-data-refresh-30m-production` Cron job
- no production pipeline runs, history rows, or forecast snapshots
- no live-domain promotion

## Final production verification

| Check | Result |
|---|---|
| Supabase project | `jcjlztdmxdzrbnbrnmmn` |
| Database role | `smartbets_production_app` |
| Role scope | Login only; no superuser/create-db/create-role/replication/inheritance; no direct Cron or Vault access |
| Prediction scope | Select/insert only; no update/delete; official insert policy requires `user_id=0` |
| Migrations | 8 installed and recorded in the Supabase migration ledger |
| Final deployment | `dpl_7v6KvxHuNABuKrQiPG1tjXVHaLeN` |
| Deployment URL | `https://smartbetsports-4vfc50um2-chrishamie141s-projects.vercel.app` |
| Commit | `1110c56a2ff18ee4c1d01baff45363c9b413103b` |
| Protected readiness | `ready=true`, blockers `[]` |
| Runtime database identity | `smartbets_production_app` on the Supabase transaction pooler |
| Production scheduler | Absent (`cronJob=null`) |

The live production domain and existing Cron jobs were not changed.

## Data-preservation evidence

Before/after production counts are identical:

- 105 official NFL system predictions
- 258 NFL prediction-history rows
- 135 NFL game results
- 262 NBA prediction snapshots
- 58 NBA settlements

NFL V2 model logic was not changed. No NFL prediction, grade, NBA record, or
existing schedule was altered.

## Staging evidence

The latest specifically recorded unattended staging run started at
**2026-10-29 11:00:00.193098 UTC / 07:00:00.193098 EDT** and durably succeeded
at **11:00:10 UTC / 07:00:10 EDT**. It reported 0 inserts, 28 duplicates,
0 conflicts, 14 unchanged forecasts, and 0 changes to official predictions.

Staging contains 107 completed games / 214 team-history rows and 14 current,
frozen Week 8 forecasts. It runs through Supabase Cron and deployed Vercel
infrastructure with no dependency on a personal computer or Codex session.

## Residual risk

The production Supabase project is on a Free plan and has no managed backup.
The migration was additive and all measured baselines were preserved, but a
restorable-backup plan is recommended before long-term production reliance.

## Activation gate

Activation was not performed. It still requires explicit founder approval to:

1. merge/promote the reviewed commit;
2. temporarily open the installer gate and install exactly one production Cron;
3. close the installer gate again;
4. enable refresh;
5. observe and validate the first scheduled production run.

The complete evidence, credential-remediation note, rollback steps, and exact
activation procedure are in
[`PRODUCTION_PREPARATION_UPDATE.md`](./PRODUCTION_PREPARATION_UPDATE.md).
