# SmartBets daily social automation

The production API owns publishing. Vercel calls `/api/cron/social-daily` at
13:00 UTC each day and supplies `CRON_SECRET`. The endpoint refuses requests
when the scheduler flag is off, the source is stale, the company account does
not match, the post is duplicated, or either publication safety switch is off.

The laptop never publishes. At 08:30 local time, the `SmartBets Social Source
Sync` task reads the frozen Week 1 experiment in read-only mode, verifies the
immutable Week 3 baseline, creates a signed aggregate envelope, and uploads it
to `/api/cron/social-source`. The cloud stores only whitelisted aggregate
evidence in separate `social_*` Postgres tables. RLS is enabled and browser
roles have no grants. Neither `predictions.db` nor the Week 1 experiment is
written by this workflow.

The campaign has 28 distinct research topics. After a complete rotation, posts
receive a date label and may recur only after the prior 27 posts. Eight topics
per rotation include the company URL; the other twenty are link-free. A fresh
source is required every 36 hours, so a stopped laptop causes publishing to
fail closed instead of repeating old performance claims.

## Operations

```powershell
# Install or inspect the read-only source synchronization task.
powershell -ExecutionPolicy Bypass -File tools/social-source-task.ps1 -Action Install
powershell -ExecutionPolicy Bypass -File tools/social-source-task.ps1 -Action Status

# Upload a fresh signed aggregate without publishing.
.\.venv\Scripts\python.exe -m backtesting.social_marketing remote-sync `
  --week-db backtesting/market_capture/nfl_2026_reg1_v1.db

# Read cloud status without publishing.
.\.venv\Scripts\python.exe -m backtesting.social_marketing remote-status

# Disable the laptop source task. To stop cloud publication immediately, set
# SOCIAL_SCHEDULER_ENABLED=false in Vercel Production and redeploy.
powershell -ExecutionPolicy Bypass -File tools/social-source-task.ps1 -Action Disable
```

Treat `FAILED`, `UNKNOWN`, `PUBLISHING`, `SOURCE_STALE`, and
`LAST_DELIVERY_REQUIRES_REVIEW` as operator alerts. An `UNKNOWN` delivery must
be reconciled against its X post ID; it must never be blindly retried.

## Event engine and bounded workers

The daily endpoint is backward compatible, but now orchestrates a bounded event
discovery and queue pass. Dedicated idempotent endpoints are also available for
deployments that need a more frequent cadence:

When `SOCIAL_SERVER_SOURCE_REFRESH_ENABLED=true`, discovery first rebuilds and
signs the whitelisted social snapshot from the canonical server-side NFL
experiment, frozen predictions, pre-kickoff markets, results, and grades. The
refresh is fail-closed on the Week 3 integrity hash and never writes to sports
evidence tables. This removes the local-worker dependency in production.

| Endpoint | Purpose | Default gate |
| --- | --- | --- |
| `/api/cron/social-discover` | Store grounded events, scores, queued and skipped opportunities | `SOCIAL_AUTOMATION_ENABLED` |
| `/api/cron/social-process` | Materialize/process at most two due opportunities | `SOCIAL_SCHEDULER_ENABLED` plus all publishing gates |
| `/api/cron/social-metrics` | Collect available X metrics for recent published posts | `SOCIAL_METRICS_ENABLED` |

Every endpoint requires `Authorization: Bearer <CRON_SECRET>`. Add extra Vercel
cron entries only after confirming plan limits and function duration. Queue work
is intentionally resumable; a single invocation never generates unlimited media.

Deploy with discovery enabled, `SOCIAL_DRY_RUN=true`, videos and replies off.
Review the Social Operations screen before enabling any public-write setting.

Production secrets belong only in Vercel's encrypted Production environment.
The local synchronization and signing secrets live in the ignored,
access-restricted `.runtime/week1/social-local.json`. Never commit either.
