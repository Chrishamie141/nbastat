# Week 1 laptop operations (X disabled)

Run commands from `C:\Users\chris\Desktop\nbastats\nbastat`.
The existing `NFL-2026-REG1-v1` database is reused, never created or refrozen.
The original Week 1/social implementation is preserved in commit `58da298`.

## Install and operate

The project `.venv` is used, not whichever Python happens to be on PATH. On a new
checkout only, create it with `py -3.14 -m venv .venv`, then install dependencies:
`.\.venv\Scripts\python.exe -m pip install -r requirements.txt`.
The existing local `.env` must supply `THE_ODDS_API_KEY`; never put it in task arguments.

```powershell
# Preview installation without creating anything:
powershell.exe -NoProfile -File .\tools\week1-task.ps1 -Action Install -WhatIf
# Offline validation, exclusive verified backup, local draft setup, register/start task:
powershell.exe -NoProfile -File .\tools\week1-task.ps1 -Action Install
# Status (read-only database inspection, plus local lock probe):
powershell.exe -NoProfile -File .\tools\week1-task.ps1 -Action Status
# Stop gracefully and disable automatic launches:
powershell.exe -NoProfile -File .\tools\week1-task.ps1 -Action Stop
# Drain the current worker, enable task, and start a fresh worker:
powershell.exe -NoProfile -File .\tools\week1-task.ps1 -Action Restart
```

Installation refuses to overwrite an existing task. If Windows denies registration,
run the same Install command in an elevated PowerShell **as the same Windows user**;
no credentials are collected or stored by the script. Do not delete/recreate the DB.

Task `SmartBets-Week1-2026` runs hidden at user login. The separate short task
`SmartBets-Week1-2026-Recovery` checks every five minutes and wakes the laptop when allowed;
it returns success without launching anything if the worker is already Running/Queued,
and respects an explicitly Disabled worker. Native failure retry is additionally configured
at one minute, three attempts, but the independently tested recovery task is the fallback.
`IgnoreNew` and a shared OS-held byte lock prevent overlapping watchers,
including manually launched `nfl_week_workflow watch` instances. The lock automatically
releases on process exit/crash. Duplicate CLI invocation is an explicit exit-0 no-op;
real configuration/integrity failures still exit nonzero. Native stdout/stderr and atomic
health JSON live under ignored `.runtime/week1/`. Stop disables both tasks before requesting a graceful stop,
waits up to two minutes, and never forcibly kills an in-flight SQLite transaction.

This is a **current-user interactive-logon task**: after a reboot, sign in once.
Locking the screen is fine; signing out is not. Keep the laptop plugged in and networked.
Windows wake timers/firmware must allow waking; a powered-off laptop cannot capture.
The task allows operation on battery but does not change system-wide sleep policy.
See Microsoft's [task logon types](https://learn.microsoft.com/en-us/windows/win32/taskschd/principal-logontype)
and [task triggers](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasktrigger).
For the narrow T−60m window, do not depend on a reboot or a five-minute recovery landing
in time. Verify HEALTHY before the window; missed observations are never backfilled.

## Safety and scheduling

`backtesting.week1_worker run` sets the legacy `DATABASE_URL` fallback to the **separate
capture database**, never production. It loads `.env` without overriding that target,
requires the Odds API key and a virtual environment, checks SQLite integrity, verifies
all 16 frozen records and pins these hashes before calling the existing lifecycle watch:

- Manifest: `ee7caccb4ab4eb920157b359b89d5cc5cad5af6a6cbd7228cf1f13f0fa1e4261`
- Prediction set: `e57e7e557a19c584dfabc097489bd39b1ea0550180c36f41bc6abe4c8d2591e9`

Market capture still uses one durable reservation per checkpoint, paired complete
markets, source/retrieval timestamps, freshness checks and SQL pregame write guards.
The first qualifying wager per profile/market remains immutable. Restart does not refund,
retry or duplicate paid checkpoint attempts. Interrupted attempts require operator review.
No current or post-kickoff prices can retrospectively qualify a wager.

The local loop checks every minute; paid requests occur only inside configured capture
windows. ESPN finals are checked at most every ten minutes, and not before the first
kickoff. Provider failures remain visible in health/status; unknown quota/auth failures
cannot silently authorize more paid calls. Local daily drafts refresh hourly and are
idempotent by UTC day. A draft failure is reported but does not stop market capture.

The installer makes an exclusive, byte-verified backup under `.runtime/week1/` while
holding the same lock and rejecting active SQLite journal/WAL sidecars. It never modifies
`predictions.db`. Week 3 is accessed **read-only** to verify marketing evidence.

Readiness/health:

```powershell
.\.venv\Scripts\python.exe -m backtesting.week1_worker status
.\.venv\Scripts\python.exe -m backtesting.nfl_week_workflow report --db backtesting/market_capture/nfl_2026_reg1_v1.db
```

Status includes the real lock state, heartbeat freshness, next checkpoint UTC, complete/
pending/missed counts, alerts, last provider status, latest successful capture, reserved
budget, last reported account usage, and verified frozen hashes. Account usage is the
provider's account-wide last observation, not a fresh query or a local cost calculation.
The scheduler audit and explanation of the former `0x800710E0` result are in
[the scheduler audit](week1-scheduler-audit.md). The original five-minute trigger on the
long-running task was replaced; fresh installations now use the two-task layout.
The raw Task Scheduler last-run result is also shown. A repeated launch can be refused
while `IgnoreNew` keeps an existing instance running; do not confuse a refused additional
launch with worker failure. Inspect the actual worker lock and fresh heartbeat. A stale
heartbeat or stopped worker needs investigation even if Task Scheduler says Running.

Initial state: 0/48 captures, 48 pending, none missed; 3/90 local credits reserved;
99,997 provider credits remaining at the September 2 preflight. No qualified wagers.
First T−24h capture: **September 8, 2026, 8:20 p.m. EDT** (September 9 00:20 UTC).
Six kickoff groups × three checkpoints × three market credits = approximately 54 credits
for captures plus the already-used 3-credit preflight, within the frozen 90-credit cap.
Missing/error responses can leave coverage incomplete; this is not a guarantee of usage.

## Saved local marketing, never X publishing

```powershell
# Activate once to use the requested plain `python` commands:
.\.venv\Scripts\Activate.ps1
python -m backtesting.social_marketing dry-run
python -m backtesting.social_marketing preview-week
# Or use .\.venv\Scripts\python.exe directly without activation.
```

The setup command creates persistent local signing material and `social-drafts.db` under
ignored `.runtime/week1/`; the installer restricts directory permissions to the current
user and SYSTEM. These are laptop-local artifacts, not OAuth credentials. Never commit
or share `social-local.json`. The worker force-sets `DRY_RUN=true`,
`SOCIAL_AUTO_PUBLISH=false`, `SOCIAL_SCHEDULER_ENABLED=false` and calls **only generation**,
never the publisher or the X API. No hosted scheduler is enabled.

The CLI falls back to this local store when `SOCIAL_DATABASE_URL` is not explicitly set.
An explicitly configured social store retains the existing CLI behavior. The saved daily
post has verified source provenance and a UTC-day idempotency key. The seven-day preview
is printed and saved to `.runtime/week1/seven-day-previews.json`; it is not queued for
publication. Future-date previews explicitly use current verified evidence, not assumed
future results; regenerate on each actual day. Dates beyond the approved 14-day campaign
require editorial review, rather than automatically extending it.

Week 3 copy is **11–4–1 across 16 frozen preseason predictions, 0 qualified wagers**.
No betting ROI is inferred from prediction accuracy and no future win rate is promised.

## Verification

Offline orchestration tests exercise the shared lock, crash cleanup, graceful restart,
idempotent replay, missing/wrong DB rejection, local config persistence, forced dry-run,
and shared CLI routing. Capture tests cover deadlines, stale/provider errors, quota,
concurrency and duplicate requests. Social tests cover verified sources, no-publication
previews and stale-source rejection. The full backend suite plus frontend tests/lint/build
are the release checks. Database SHA-256 checks before/after validation are recorded in
the task handoff; the frozen manifest/prediction hashes remain the longitudinal invariant
once legitimate captures start changing the capture DB's overall file hash.

Laptop acceptance checks on September 2: the task was installed, its hidden process
reported HEALTHY, graceful restart replaced the worker PID, and a second manual worker
was refused without disturbing the existing one. The expanded focused suite passed
106 tests; frontend tests passed 23/23, lint and production build passed. The fresh
virtual environment exposed a missing `jsonschema` requirement, now declared explicitly.
No live odds calls were needed for these checks.
The full backend run passed **682 tests, with one opt-in live-provider test skipped**
in 392.85 seconds. Three orchestration regressions added after that run's collection
also passed in the expanded 106-test focused run (12 worker tests total).

Both database files remained byte-identical through setup, tests and restart:

- Production/Week 3: `3dd28869aafbc84b354316f4df2c026ed8d0e94d0ba5fa6d7e33301866290850`
- Week 1: `e3fb636e5b06b8eaa03e76bf1f25398ac64e2a83b6de3658af64f1e7c8cafaf3`

The installer verified a backup at
`.runtime/week1/week1-before-worker-20260902-130444-661843.db` against the Week 1 hash.
One earlier verified setup backup was also retained. Runtime artifacts, signing material,
databases, logs, previews and the virtual environment are ignored and excluded from Git.
