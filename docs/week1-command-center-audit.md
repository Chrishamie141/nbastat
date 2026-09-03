# NFL Week 1 owner Command Center audit

Audit date: 2026-09-03 (America/New_York)

## Baseline

- Starting branch: `integration/smartbets-laptop-home-sync`
- Starting commit: `201015204c461b29732ea908b89c546a03a634be`
- Starting tree: clean
- Implementation branch: `codex/week1-command-center-production-pass`
- Backend: Python 3.14.2, FastAPI application version 2.0.0
- Frontend: Node 24.13.0, npm 11.6.2, Next.js 14
- Database: SQLite locally; persistent PostgreSQL required on Vercel
- Operational context: NFL 2026 regular-season Week 1
- Providers: ESPN schedule/summary (free), The Odds API (controlled checkpoint worker only)
- Owner route: `/internal/operations`

The audit records configuration as booleans only. No credential, token, database URL, or account identifier is included.

## Browser baseline

The authenticated production owner page was exercised in a browser before implementation. It loaded, but its hierarchy emphasized experiment summaries, a buyer funnel, memberships, and X activity. The primary navigation also exposed consumer Dashboard, Games, Parlays, Fantasy, History, Performance, and Account links inside the owner portal.

Observed gaps:

- no Week 1 readiness checklist;
- no actionable Week 1 game ledger;
- no prediction-run or outcome operations;
- no database, worker, provider, or freshness drill-down;
- no unified operational exception queue;
- no owner action history;
- a whole-page dependency on the signed social source;
- no panel-level recovery;
- membership/billing information competing with operational state;
- production social status initially displayed automatic publishing enabled and dry-run disabled, contrary to the required safety posture.

The production environment safety flags were immediately restored to dry-run enabled and automatic publishing disabled before feature work continued. No publish endpoint was invoked.

## Week 1 source baseline

- worker: healthy, single active PID observed;
- frozen predictions: 16/16;
- scheduled games: 16;
- checkpoints: 0 complete, 48 pending, 0 missed;
- provider preflight: ready;
- experiment integrity: pass;
- market coverage: 0/16 before the first checkpoint;
- final results and grades: 0/16 before kickoff;
- next checkpoint at audit time: 2026-09-09T00:20:00Z.

The market-coverage warning is expected before the first controlled capture and is not a readiness failure. No paid capture was triggered during this audit.

## Owner endpoint matrix

| Method | Route | Purpose | Auth | Database | Provider | Prediction | Baseline | Result |
|---|---|---|---|---|---|---|---|---|
| GET | `/api/internal/operations` | Aggregate owner control plane | internal | primary + read-only Week 1 | status only | frozen metadata | social-centric, full-page failure risk | refocused and panel-isolated |
| GET | `/api/internal/operations/health` | Operator readiness detail | internal | lightweight read | status only | artifact health | missing | added |
| GET | `/api/internal/operations/search` | Games, teams, players, issues, artifacts | internal | indirect | none | metadata | missing | added with explicit partial errors |
| GET | `/api/internal/operations/games/{game_id}` | Owner game, prediction, actuals, provenance | internal | read-only Week 1 | ESPN detail | frozen artifact | missing | added |
| POST | `/api/internal/operations/refresh` | Refresh free operational schedule | internal | action log | ESPN only | none | missing | added; paid provider false |
| POST | `/api/internal/operations/games/{game_id}/refresh` | Refresh/reconcile one game | internal | action log | ESPN only | immutable read | consumer-only action | added with lifecycle logging |
| GET | `/api/nfl/games/{game_id}` | Canonical game detail | public read | none | ESPN | frozen player artifact | healthy | retained |
| POST | `/api/nfl/games/{game_id}/refresh` | Existing scoped refresh | full access | none | ESPN | none | healthy | retained |
| GET | `/api/search` | Product catalog search | public read | none | ESPN schedule | none | partial errors already explicit | retained |

No generic model-run, paid market-capture, grading, or publishing action is exposed by the Command Center.

## State-machine decisions

- refresh running: the initiating control is disabled and labeled `Refreshing…`;
- refresh success: the dashboard reloads and action history records success;
- refresh failure: last-known-good data stays visible, an explicit error is shown, and Retry is offered;
- prediction complete: View prediction is shown; duplicate generation is unavailable;
- prediction missing: the issue is queued, but unsafe generic generation is not exposed;
- final and graded: View final & comparison replaces grading as the primary action;
- stale/error game: Reconcile replaces the ordinary refresh label;
- social safeguards incomplete or dry-run: publishing is BLOCKED and no publish action exists.

## Boundaries

- The Week 1 operational SQLite store is opened read-only by the control plane.
- Week 3 is not written or regenerated.
- The Odds API is not called by owner refresh actions.
- System A prediction panels contain no sportsbook price.
- Social metadata exposes configuration state only, never secret values or the expected account ID.

## Local endpoint and browser verification

The owner endpoints were exercised with an isolated primary database, isolated social database, authenticated internal test operator, and the real Week 1 store opened read-only.

| Route | Observed status | Observed latency |
|---|---:|---:|
| `/api/internal/operations` | 200 | 80.4 ms |
| `/api/internal/operations/health` | 200 | 32.2 ms |
| `/api/internal/operations/search?q=SEA` | 200 | 47.6 ms |
| `/api/internal/operations/games/espn-401872656` | 200 | 11,517.1 ms uncached |

The owner game-detail client therefore uses a 20-second bounded request instead of the default 12-second request. Subsequent cached reads were materially faster.

Browser smoke coverage:

- internal login redirected to the Command Center;
- owner navigation contained only Command Center and the Week 3 archive;
- no membership, subscription, billing, upgrade, or account-management widgets appeared;
- Week 1 readiness and all six summary cards rendered;
- 16/16 games and 16/16 frozen predictions rendered;
- Needs Attention distinguished INFO from actionable WARNING/CRITICAL counts;
- a safe ESPN-only operational refresh completed and was recorded in action history;
- search found the known `SEA` Week 1 game; a catalog response-shape bug discovered during the smoke test was fixed;
- the NE at SEA owner game opened with frozen winner, probability, model, generation time, and artifact hash;
- refreshing that game preserved the frozen owner prediction after a smoke-discovered state-loss fix;
- pregame context, actuals availability, comparison availability, freshness, and provenance remained separate;
- the game page explicitly reported that no paid provider was contacted;
- social publishing displayed blocked/dry-run state and provided no publish control.

No Week 1 game is final on the audit date, so the real browser could not display an authentic Week 1 final. Final/live/stale, score repair, missing-stats, comparison, and last-known-good behavior are covered by provider fixtures and backend regression tests rather than fabricated browser data.

## Final validation

- backend full suite: 710 passed, 1 skipped;
- owner/game/social/database targeted suite: 73 passed;
- frontend tests: 28 passed;
- frontend lint: passed with no warnings or errors;
- Next.js production build: passed, including `/internal/operations` and `/internal/games/[gameId]`;
- Python compile checks: passed;
- `git diff --check`: passed;
- authoritative `predictions.db` remained byte-identical across database-isolation tests;
- Week 1 manifest and frozen prediction hashes remained verified;
- frozen preseason Week 3 integrity: PASS, 16 predictions, expected hash equals computed hash;
- production browser safety recheck: automatic publishing disabled and dry-run enabled;
- no social publish action or paid odds capture was invoked.
