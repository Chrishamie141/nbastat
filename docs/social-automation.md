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

Production secrets belong only in Vercel's encrypted Production environment.
The local synchronization and signing secrets live in the ignored,
access-restricted `.runtime/week1/social-local.json`. Never commit either.
