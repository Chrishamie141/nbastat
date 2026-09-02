# NFL coverage-first experiments

## Scope and pre-implementation audit

Week 3 (2026 preseason) is complete and must remain untouched. This workflow collects
forward **game markets**, not player props, and neither generates predictions nor tunes
models. Its first experiment is a market-coverage baseline. Model comparison/registry
promotion can consume these snapshots in a later, separately authorized step.

Existing implementation audited:

- `nfl_product_service._schedule` maps ESPN provider weeks to canonical display weeks.
  Reused for slate discovery, with its production refresh-log callback suppressed.
- `nfl_experiment_service.is_strictly_pregame` is reused for timestamp validation.
- `backtesting.game_matching.match_game` and `normalize_team` are reused for canonical
  home/away/date reconciliation. Ambiguous matches are rejected.
- `nfl_providers._fetch_json_structured` retains safe quota headers and sanitizes errors.
  Reused directly; the new workflow does not introduce another HTTP client.
- The dashboard `_odds_by_game` path independently polls a five-minute cache and logs
  into production. It is intentionally **not** used for controlled capture.
- `_insert_market_observation` collapses unchanged prices and stores combined market
  summaries. It cannot represent repeated checkpoint evidence with individual paired
  bookmaker markets, so it is left unchanged for existing consumers.
- `capture_nfl_live_player_props` is an event-by-event player-prop shadow collector with
  an explicit paid cap. Its budgeting pattern informs this workflow; its paid per-event
  strategy is inappropriate for the cheaper league-wide featured game-market endpoint.

## Architecture and isolation

`python -m backtesting.capture_nfl_game_markets` provides `create`, `tick`, `watch`,
`report`, and `reconcile-quota`. No production routes or existing schemas are modified.

Use **one dedicated SQLite file per slate**, shared by all workers for that experiment:
`backtesting/market_capture/nfl_2026_reg1_v1.db`. Never create multiple stores for the
same slate to bypass budget accounting. SQLite is deliberate for the authoritative local
laptop; PostgreSQL capture storage is not supported by this command.

The process refuses `predictions.db`, its resolved aliases/hard links, pre-existing
destinations during creation, and databases without its application ID. It does not use
`DATABASE_URL`, initialize prediction tables, refresh a dashboard, or grade experiments.
2026 preseason Week 3 is explicitly prohibited. Existing suite-wide temporary DB guards
remain active during tests.

`create` freezes a SHA-256-addressed game manifest, checkpoint schedule, market set,
credit ceiling, and model reference. This is a **market-only experiment**, not a new set
of model predictions. Only future scheduled games are accepted. A manifest mismatch
halts operations. Creation is exclusive, not an upsert. Source and capture metadata are
append-only; immutable-table triggers reject edits/deletions.

New standalone tables:

| Table | Purpose |
| --- | --- |
| `experiment` | Immutable manifest/hash/config; mutable durable budget, quota and status |
| `games` | Frozen canonical game IDs, teams and kickoff |
| `checkpoints` | Due/deadline and persisted outcome per game/checkpoint |
| `requests` | Pre-request reservations, safe response headers/hash, completion/error state |
| `captures` | Paired bookmaker-market evidence, source/retrieval/storage timestamps and provenance |
| `quotes` | Queryable outcome side, line and American price, linked to capture evidence |

Each capture records provider, endpoint without credentials, provider event ID, canonical
game, bookmaker key/title, market type, source time, retrieval time **after the complete
response**, storage time, both kickoff sources, match strategy, and market payload hash.
The request stores the canonical JSON response hash, not a raw HTTP-byte archive.

Capture identity is `(game, checkpoint, bookmaker, market)`. Quote identity adds side.
Retries/restarts cannot duplicate them. Unchanged prices at **different** checkpoints are
distinct evidence. Coverage requires two valid opposing outcomes for each market; a
single side or malformed line does not count. Different markets may come from different
books; this does not imply a same-book three-market bundle.

## Checkpoints and budget

| Checkpoint | Allowed late window | Purpose |
| --- | --- | --- |
| T−24 hours | 30 minutes | Broad early coverage |
| T−6 hours | 15 minutes | Updated pricing |
| T−60 minutes | 5 minutes | Safely pregame final research snapshot |

T−60 is **not** labeled a closing line. No last-second polling is performed. The watcher
wakes locally once a minute, requests only when a checkpoint is due, and batches every
due game into one NFL-wide request for `h2h,spreads,totals`, US region, American odds.
There is a five-minute minimum interval between paid requests and no automatic retry of
an attempted checkpoint. A bad/partial response remains visible; the next checkpoint is
the next opportunity. Repeat captures yield local budget to future uncovered games.

The Odds API currently charges one credit per region per requested featured market:
three markets × one region = **3 credits per batch**. One batch serves all due games.
See [official V4 documentation](https://the-odds-api.com/liveapi/guides/v4/).

Let G be the number of distinct kickoff groups. If all games in each group share checkpoint
windows, a complete slate needs at most **3G requests / 9G credits**, often less when
windows overlap. Examples (planning estimates, not measured API use):

- One simultaneous 16-game slate: 3 requests, 9 credits.
- Six kickoff groups: 18 requests, 54 credits.
- Ten kickoff groups: 30 requests, 90 credits.
- All 16 games start separately: up to 48 requests, 144 credits; a 90-credit cap will
  intentionally leave some repeat captures unfilled.

The immutable default ceiling is **90 credits** for the whole experiment, not per day or
per process. A `BEGIN IMMEDIATE` transaction reserves three credits and marks all due
checkpoints `IN_FLIGHT` **before** the request. Reservations are never refunded, including
failed, interrupted, or provider-free empty responses: conservative accounting bounds
spend across crashes/restarts. Concurrency tests verify only one request is reserved.

`x-requests-remaining`, `x-requests-used`, and `x-requests-last` are retained. Six account
credits are held in reserve once remaining quota is known. The first call can bootstrap
quota under the local cap, but you should reconcile the known account balance before
starting. Missing/unparseable remaining headers halt further calls (`QUOTA_UNKNOWN`).
A reported cost above three also halts (`COST_CHANGED`). `Retry-After` integer seconds
are honored. Auth/quota errors stop the watcher; transient failures are recorded, with
no same-checkpoint retry. Missing quota metadata after a network failure requires manual
reconciliation before continuing.

The cap is **local to this store**, not an account-wide lock. Disable other paid polling
jobs/dashboard refreshes using the same API key during a capture campaign, or allocate
them a separate budget/key. Other consumers can reduce account credits between responses.
The workflow never purchases credits or resets a quota automatically.

## Hard pregame boundary

Only explicitly timezone-aware source and kickoff timestamps are accepted; naive local
times are rejected rather than interpreted as UTC. Only source and retrieval timestamps
strictly before kickoff qualify. Source timestamps
must not be in the future or over 15 minutes old. A changed provider kickoff by more than
60 seconds is rejected as `SCHEDULE_CHANGED`; a smaller discrepancy uses the earlier
kickoff. Kickoff and checkpoint deadline are checked again after HTTP completion, on each
insert, and by SQLite triggers using the connection's current clock. Backdating retrieval
time cannot bypass the write-time check. Quotes have the same insert protection.

After kickoff, **no market or quote rows** can be inserted, updated, or deleted through
this workflow. Control-plane statuses (missed/blocked/error/quota) may still be persisted
to explain missing evidence. Existing prices are never replaced by current or postgame odds.

The clock must be correct (Windows time synchronization enabled). The manifest is not
silently rescheduled: review an actual schedule change and establish a separately named
replacement experiment/configuration only after checking budget and provenance. An early
kickoff absent from both supplied schedule sources cannot be inferred by this collector.

## Commands: next regular-season Week 1

These are launch instructions, **not commands run during implementation**. No live Odds
API credits were spent during implementation/testing. Configure `THE_ODDS_API_KEY` in
your environment or existing private `.env`; never put it on the command line.

```powershell
cd C:\Users\chris\Desktop\nbastats\nbastat

# Free ESPN discovery and a NEW immutable market experiment; refuses an existing file.
py -3.14 -m backtesting.capture_nfl_game_markets create --db backtesting/market_capture/nfl_2026_reg1_v1.db --season 2026 --phase regular --week 1 --from-espn --credits 90

# Offline status: reports checkpoint/game/market coverage and alerts.
py -3.14 -m backtesting.capture_nfl_game_markets report --db backtesting/market_capture/nfl_2026_reg1_v1.db

# Start the foreground checkpoint worker. Keep this PowerShell session/laptop running.
py -3.14 -m backtesting.capture_nfl_game_markets watch --db backtesting/market_capture/nfl_2026_reg1_v1.db --allow-paid
```

Alternatively invoke `tick --db ... --allow-paid` once a minute through your existing
operator scheduler (do not run separate capture stores). No operating-system task is
installed automatically. `watch` resumes the same ledger after restart. It cannot capture
missed time while the laptop is asleep, offline, or shut down.

Offline creation uses `--games-json slate.json` instead of `--from-espn`. Supply an array
of canonical records with `game_id`, `home_team`, `away_team`, `kickoff_time` (ISO UTC),
and `status: "scheduled"`; optional season/season_type/display_week must match the scope.

After verifying actual remaining credits on the provider dashboard and fixing any key or
network issue, explicitly reconcile quota, then restart `watch`:

```powershell
# Replace 120 with the ACTUAL verified available account balance, not a desired budget.
py -3.14 -m backtesting.capture_nfl_game_markets reconcile-quota --db backtesting/market_capture/nfl_2026_reg1_v1.db --remaining 120
```

Reconciliation does not increase the frozen local cap, refund reservations, replay failed
checkpoints, or alter existing evidence. Never recreate a store to erase spend history.

## Status and alerts

`report` is network-free and read-only. JSON exposes coverage for every game/checkpoint,
market names, complete checkpoint counts, unique games with any valid market, request
states, quota status and `last_status`. The `alerts` array includes missed captures,
partial/no markets, stale data, kickoff blocks, changed schedules, malformed responses,
authentication, quota, rate-limit, upstream and network errors, and interrupted attempts.
Quota/auth blocks before a request appear in top-level quota/last-status and `tick` output.

`tick` exits 2 on provider/auth/quota failures for scheduler visibility.
`watch` prints changed status to stdout and exits 2 when operator action is needed for
budget/quota/auth. It sends no emails, Slack messages, or other external notifications.
An incomplete checkpoint is never reported as complete. A completed quote set does not
claim any ROI, model performance, or wagering eligibility.

## Validation

```powershell
py -3.14 -m pytest -q tests/test_capture_nfl_game_markets.py tests/test_nfl_experiment_service.py tests/test_nfl_product_service.py tests/test_database_isolation.py tests/test_capture_nfl_live_player_props.py
```

All provider responses are mocked. Tests cover batching, provenance/paired quotes,
idempotency, source freshness, delayed/post-kickoff responses, database-level backdate
rejection, quota ceilings/headers/reconciliation, rate limiting, crashes/concurrent workers,
coverage-first allocation, Week 3/production-file refusal, read-only reports, and discovery
without legacy production writes. The production DB file hash is checked separately before
and after the full suite; no validation should initialize or migrate it.

Implementation validation on 2026-09-02: full backend run **633 passed, 2 skipped**
(PostgreSQL driver unavailable and live-first provider opt-in). After final safety/CLI
additions, the capture-specific suite passed **47 tests**. Frontend **23 tests passed**,
lint passed, and the Next.js production build passed. `git diff --check` passed.
No live Odds API calls were made. The authoritative `predictions.db` SHA-256 before and
after implementation/tests was unchanged:

```text
3dd28869aafbc84b354316f4df2c026ed8d0e94d0ba5fa6d7e33301866290850
```

A final read-only check confirmed 16 frozen predictions, 11 wins / 4 losses / 1 push,
three Week 3 market observations, and the unchanged baseline hash
`a8a405ba262ef59bedb1b7bfcdf1ca4a7c0bf78cafe4b0ff0c1268b7d8b2412a`.
