# NBA production refresh activation — 2026-10-29

## Operational status

- Production ingestion: **ACTIVE**
- Scheduler: **ACTIVE**, Supabase Cron job `smartbets-nba-data-refresh-daily-production` (job 7)
- Local schedule: **03:00 America/New_York daily**
- UTC candidates: `07:00` and `08:00`, protected by an Eastern-local-time and local-calendar-day guard
- Next scheduled execution: **2026-10-30 07:00 UTC / 2026-10-30 03:00 EDT**
- Latest unattended Cron execution: **not yet observed**; activation was completed after the October 29 scheduled window
- Cloud independence: **YES**; Vercel, Supabase Postgres, Supabase Cron, Vault, and `pg_net` provide the ongoing path

## Controlled production verification

- Run ID: `nba-auto-de6fb1eb61624b878f9229b095770f47`
- Started: **2026-10-29 21:16:32 UTC / 17:16:32 EDT**
- Completed: **2026-10-29 21:17:28 UTC / 17:17:28 EDT**
- Result: **SUCCEEDED**
- Provider schedule records fetched: **237**
- Final events validated across the ingestion window: **135**
- Completed 2026-27 regular-season games in canonical history: **68**
- Duplicate/changed canonical games on the repeat verification run: **0**
- Upcoming games evaluated: **102**
- New forecast snapshots on repeat verification: **0**
- Unchanged/idempotently retained forecasts: **102**
- Personnel refresh: **FRESH**, 30/30 teams, 519 player rows

The first successful activation cycle created 102 `LATEST_FORECAST` snapshots. The repeat run above proved that the refresh is idempotent and does not create duplicate snapshots when model inputs are unchanged.

## Data and forecast cutoff

- Latest completed regular-season game: `espn-401909892`, LAL 103 at LAC 87
- Game start: **2026-10-29 02:30:00 UTC / 2026-10-28 22:30:00 EDT**
- Source retrieval / feature cutoff: **2026-10-29 10:29:40 UTC / 06:29:40 EDT**
- Latest forecast snapshot generation: **2026-10-29 20:53:34 UTC / 16:53:34 EDT**
- Every current latest forecast records **68** eligible regular-season evidence games
- Latest evidence game: `espn-401909892`
- Latest selections: **100 home, 2 away**

## Integrity and security

- Model version: `nba-game-elo-baseline-v1`
- Elo formula, coefficients, K-factor, and home-court parameter: **unchanged**
- Existing official predictions remain separate from versioned `LATEST_FORECAST` rows
- Official prediction count after activation: **262**
- Official prediction integrity digest: `b691568608a4b5574dde3a2e8f13327ce`
- Historical settlements and grading were not rewritten
- Runtime database role: `smartbets_production_app` (least privilege)
- NBA bearer is stored as a Vercel Secret and in Supabase Vault; it was rotated after activation
- Installation flag: **disabled**
- Temporary migration endpoints and bundled migration script: **removed from the runtime deployment**
- Installer endpoint verification: HTTP 409 (disabled)
- Production readiness endpoint: `ready=true`, no blockers

## Provider recovery

The primary ESPN monthly schedule feed began returning HTTP 403 from Vercel egress during activation. The pipeline now retries with bounded backoff and falls back to ESPN's dated CDN scoreboard feed. The fallback was verified in the successful 237-record controlled run. Missing or unverifiable provider days still fail closed instead of being treated as complete.

## Model-quality limitation

Fresh current-season results now demonstrably reach the Elo model, but this does not promote the model for wagering. The registry still classifies `nba-game-elo-baseline-v1` as `RESEARCH_ONLY`. The current 100-home / 2-away split shows that home-court dominance remains a model-quality concern rather than a stale-data problem. Personnel data is collected and monitored but is not consumed by this Elo model.

## Verification

- Public `/api/health`: healthy; production Postgres healthy; no missing tables
- Final locked Vercel deployment: `dpl_GnvePpXaLEGDvMAr4GpWYFqPQug9`
- Final-deployment error/fatal log scan: no entries
- NBA-focused tests: 42 passed
- Full backend suite: 920 passed, 1 skipped, 1 unrelated pre-existing failure in `test_analyze_information_architecture.py` (removed `Fantasy Football` label)

## Remaining observation gate

The controlled production execution is verified. The first unattended Supabase Cron execution is pending at 2026-10-30 03:00 EDT; until its run ledger entry exists, unattended execution is configured but not yet empirically observed.
