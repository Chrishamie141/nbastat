# Week 1 Windows Task Scheduler audit — September 2, 2026

## Root cause, verified locally

`2147946720 = 0x800710E0 = HRESULT_FROM_WIN32(4320)`; Windows identifies this as
`ERROR_REQUEST_REFUSED`: the operator or administrator refused a request.
It was **the repeated launch request**, not the running Python worker, that failed.
The original task combined an indefinitely running action with a five-minute repeating
trigger and `MultipleInstancesPolicy=IgnoreNew`. Each repeat requested another instance,
which Windows refused while preserving the running instance. `LastRunTime` advanced to
the rejected launch time; it was not the worker's process start time.

A disposable task using the same hidden PowerShell action style and IgnoreNew settings
reproduced this sequence on this laptop:

| Step | LastTaskResult | Actual state |
| --- | --- | --- |
| Initial long-running action | 267009 / 0x00041301 | Running |
| Second launch while active | 2147946720 / 0x800710E0 | Same action still Running |
| Action completes normally | 0 | Ready |

Microsoft documents [IgnoreNew](https://learn.microsoft.com/en-us/windows/win32/api/taskschd/ne-taskschd-task_instances_policy)
as refusing to start another instance while one is already running.
Task Scheduler Operational history was **disabled** when inspected, with no historical
events available. It was not enabled or otherwise changed. Conclusions are based on
configuration, live process/heartbeat observations and the controlled reproduction,
not invented historical events.

## Worker lifecycle and safety

Before repair, there was one watcher implementation, PID **28864**, continuously running
since 17:10:16 UTC. Its parent Python PID 19576 was the Windows virtual-environment
launcher, itself under hidden PowerShell PID 13520. Two Python executables in that single
parent-child chain do not mean two independent watchers. The OS-held byte lock remained
held; heartbeat epochs advanced from 1788369558.3081057 through 1788369979.4075975.
No unexpected exit/restart or worker failure was observed. Historical completeness is
limited by disabled event history.

After one deliberate graceful restart, the chain became PowerShell 14728 → venv launcher
21444 → watcher **29436**, started 17:26:53 UTC. No forced process termination was used.

Both protected SQLite files remained byte-identical across the audit and restart:

- `predictions.db`: `3dd28869aafbc84b354316f4df2c026ed8d0e94d0ba5fa6d7e33301866290850`
- Week 1: `e3fb636e5b06b8eaa03e76bf1f25398ac64e2a83b6de3658af64f1e7c8cafaf3`

Week 1 retains 16 frozen predictions, verified manifest/prediction hashes, zero qualified
wagers, 48 pending checkpoints, no missed checkpoints or alerts, provider READY.
The next capture is September 8, 20:20 EDT / September 9, 00:20 UTC.
No paid odds calls, early captures, experiment edits or X setting changes were made.

## Repair

- Removed only the repeating trigger from the long-running worker task; retained login,
  user identity, action, venv, working directory, battery/wake policy and IgnoreNew.
- Added `SmartBets-Week1-2026-Recovery`: a short five-minute recovery/wake task that checks
  scheduler state without opening databases or importing Python application code. It
  exits 0 when the worker is already Running/Queued or deliberately Disabled, and starts
  it only when stopped. A missing worker task is an error, not silently treated as success.
- Configured additional native failure retry at one minute × three attempts. A disposable
  demand-start failure did **not** restart during the probe's roughly 70-second observation,
  so native retry alone is not relied upon. The separate timed recovery task successfully
  restarted that failed dummy task: worker exit 7 → recovery scheduled automatically →
  worker exit 0 and recovery exit 0. No SmartBets database was used in the failure probe.
- Stop disables both tasks before draining the worker; Restart reenables both.
- Direct duplicate Python CLI invocation now returns `ALREADY_RUNNING`, exit 0. Real
  failures remain exit 2. The module entrypoint uses the canonical Python module so
  `python -m` and imported lifecycle code share the same typed duplicate exception.
- Status shows both tasks, raw/hex result, a result label, and ISO timestamps.
- The original task XML is backed up in ignored `.runtime/week1/task-before-repair-*.xml`.

The final scheduler states immediately after restart were:

- Worker: **Running**, `267009 / 0x00041301` (**RUNNING**, not a failure).
- Recovery: **Ready**, `0` (**SUCCESS**); timed recovery was observed completing without
  changing the active watcher PID.

Reboot itself was not performed. The worker's user-specific logon trigger was retained;
after reboot the same user must sign in. The recovery task also uses InteractiveToken
and StartWhenAvailable. This does not claim operation before login, when powered off,
without network, or when Windows/firmware disallows wake timers.

## Files and repeatable verification

Changed: `tools/week1-task.ps1`, `backtesting/week1_worker.py`,
`tests/test_week1_worker.py`, `docs/week1-unattended.md`.
Added: `tools/week1-task-recovery.ps1`, `tools/test-week1-scheduler.ps1`, this report.
The disposable audit tasks were unregistered after testing; harmless marker files were
retained under ignored `.runtime/scheduler-audit/`. No existing tasks were deleted.

```powershell
# Anytime health check; no provider calls or database writes:
powershell.exe -NoProfile -File C:\Users\chris\Desktop\nbastats\nbastat\tools\week1-task.ps1 -Action Status

# Repeat the isolated Windows reproduction/recovery test, ~2 minutes, no SmartBets data:
powershell.exe -NoProfile -File .\tools\test-week1-scheduler.ps1

# Mocked/local application regression tests:
.\.venv\Scripts\python.exe -m pytest tests/test_week1_worker.py tests/test_database_isolation.py tests/test_nfl_week_workflow.py tests/test_capture_nfl_game_markets.py -q
```

The application checks cover typed duplicate no-ops (including the real module entrypoint),
unmasked genuine errors, shared locks, crash cleanup, isolation, restart idempotency,
pregame boundaries and capture quotas. No model, market, prediction, social publication,
or database schema changes are part of this repair.

Final validation: **90 tests passed in 29.86 seconds**; `git diff --check` passed.
New PID 29436's heartbeat advanced from 1788370013.4128325 to 1788370133.6504564
(17:26:53 → 17:28:53 UTC) without another restart. The main task stayed Running with
267009; the recovery task reported success (0). Source changes are left uncommitted for
review; no merge or push was performed.
