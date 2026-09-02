# SmartBets laptop/home integration

## Protected history and integration

- Integration branch: `integration/smartbets-laptop-home-sync`.
- Protected laptop branch: `codex/production-readiness-operational-audit`.
- Protected laptop HEAD: `02fa1cdc52fcf70b73b0af6ba50bc49e19e332c3`.
- Backup tag: `backup/smartbets-laptop-before-home-sync-20260902`.
- GitHub/home source: `origin/codex/nfl-team-logos-layout` at `4bf59db3347156283dedc50e1cefa136c3e819fc` (also fetched origin/main).
- Common ancestor: `6e6bdbd`.
- Merge commit: `37407d0`, with both laptop and home heads as parents. No cherry-picks, rebases, branch overwrites, or main merge.
- The unavailable `cbda6e5` patch/object was not used. Isolation is a new implementation of the user's stated requirements, not a claim of byte-identical reconstruction.
- No push or deployment was performed.

All five laptop-only commits are preserved:
- `38f066b` Add NFL game breakdown and lifecycle refresh
- `b6f9114` Harden production readiness and game analysis flows
- `4e0b9bd` Bundle production game analysis artifacts
- `100945d` Fix Vercel artifact include glob
- `02fa1cd` Bundle compact production game catalogs

All seven GitHub/home-only commits are preserved:
- `4145d0d` Build week-first NFL analysis and fantasy flows
- `02588d7` Fix production API authentication startup
- `3a14f07` Add resilient NFL schedule fallback
- `5caf4de` Add NFL team logos and probability layout
- `ce4e363` Add NFL team logos and probability layout
- `d336a4c` Add Week 3 experiment operations and integrity dashboard
- `4bf59db` Merge remote-tracking branch origin/main into codex/nfl-team-logos-layout

## Conflict resolutions

Seven content conflicts occurred among eight overlapping files:

| File | Resolution |
| --- | --- |
| backend/app/main.py | Retained readiness, search, live detail, refresh, observability and normalized errors; added weekly board/context, historical snapshots, depth charts and internal experiment routes. Static game-history route is registered before the dynamic game-ID route. |
| frontend/app/analyze/page.jsx | Retained the home's new NFL analysis choices and NBA flow, with laptop contextual loading/retry controls. Preserved the complete original analysis/fantasy draft-board flow at /analyze/classic and linked it from the new flow. |
| frontend/app/dashboard/page.jsx | Added a weekly NFL board using NflMatchup before the laptop's existing catalog search, lifecycle filters, manual refresh, featured game and account activity. Weekly request failures are explicit; stale weekly responses are ignored after selection changes. |
| frontend/app/games/[slug]/page.jsx | Kept the new weekly pick/market-evidence detail and added a link to the preserved /nfl/games/[gameId] live breakdown. Added a Suspense boundary for search parameters. Replaced the old unconditional redirect with the richer view; its destination remains available. |
| frontend/app/history/page.jsx | Combined Past Games/original pregame snapshots with explicit unavailable-vs-empty states, request cleanup and loading-disabled tabs. |
| frontend/components/auth/AuthProvider.jsx | Kept stable useCallback/useMemo authentication functions; protected both /internal and /nfl routes in addition to existing routes. Backend internal authorization remains enforced. |
| frontend/lib/api.js | Kept laptop timeouts, credentials, normalized error handling, readiness/search/refresh APIs, and added home weekly/experiment/depth-chart endpoints. |

The eighth overlap, backend/app/database.py, merged automatically. Review confirmed both the laptop adapter and the home's POSTGRES_URL_NON_POOLING fallback remain. Its test-only connection guards were added afterward.

No unresolved or deliberately deferred source conflicts remain. The duplicate histories of logo commits were preserved rather than rewritten. Existing tests were not deleted or weakened to obtain passing results.

## Recreated test-isolation behavior

- Root conftest.py creates a disposable SQLite database and configures environment overrides before test modules are imported.
- An autouse fixture gives every test its own fresh schema-initialized database, in a sibling directory separate from the test's disposable cache/files directory.
- Both backend and legacy prediction storage use the isolated target. Legacy default paths are resolved at call time rather than captured in default arguments.
- The active test may connect only inside its registered temporary database/work roots, or to SQLite-managed temporary/in-memory stores. Prior tests' and collection-time roots are not usable during a test.
- Central guards reject the repository predictions.db, including resolved paths, file URIs and same-file aliases. A SQLite audit hook covers direct connections; a connection authorizer blocks ATTACH escapes.
- PostgreSQL is rejected during isolated tests before backend connection attempts. Direct psycopg entry points are also blocked when that optional driver is installed. This suite has no live-PostgreSQL opt-in configuration.
- Production routing is unchanged outside pytest/test-isolation mode. The test bootstrap is excluded from serverless upload.
- Regression tests cover collection-time writes, actual auth/depth-chart/prediction persistence, fresh per-test databases, URI/alias/ATTACH rejection, PostgreSQL rejection, and byte-identical production database contents.
- The tests intentionally use synthetic/future experiment data in temporary stores. They do not grade or initialize the local production database.

## Validation

Final full-suite results: 592 passed, 2 skipped in 291.48 seconds using `py -3.14 -m pytest -q -ra --tb=short`.
Skips: optional psycopg driver not installed; live-first NFL provider test requires explicit opt-in.
Dedicated isolation/startup/integration run: 17 passed, 1 skipped.
Focused safety/integration/cache/replay run: 53 passed, 1 skipped.
Frontend: 23 passed; lint has no warnings or errors.
Production frontend build: PASS (Next.js 14.2.35).
Backend startup: TestClient lifespan plus /api/health and /api/readiness pass against a temporary database.
Python compileall and git diff --check: PASS.
The first full run found two cache-cleanup failures caused by storing the fixture DB inside the cache test directory. Moving it to a separate per-test sibling directory resolved both without changing or weakening the cache tests; the final full run above includes that fix.
No live paid-provider, production PostgreSQL, billing mutation or deployed-site operation was performed.

## Database and frozen Week 3 status

Repository predictions.db remains ignored and untracked. It was not copied, replaced, migrated, initialized or graded.

Before and after source integration/testing, its SHA-256 is:

```text
a5b5d2261a8824d9d22afdf18874ea4b686853cb5b8ecf4886d8ed950ee6fbdc
```

The experiment service and internal Week 3 dashboard are unchanged relative to the GitHub/home source (verified with git diff). The expected frozen baseline hash remains:

```text
a8a405ba262ef59bedb1b7bfcdf1ca4a7c0bf78cafe4b0ff0c1268b7d8b2412a
```

Synthetic tests prove snapshot immutability through lifecycle changes, hash-mismatch grading rejection, and idempotent flat-unit grading. **The actual frozen 16-row Week 3 baseline is NOT verified locally:** this laptop has the older database without the experiment tables. Missing evidence correctly fails closed in tests. No replacement predictions were generated.

Intentionally excluded: home-PC database, missing commit/patch object, private credentials, model changes/promotion, production deployment, and changes to main.

## Later, separate authoritative database transfer

Do not run these during source integration. First stop application/test processes on both machines and ensure the source SQLite store has no pending WAL writes. Transfer the cleaned database from:

`C:\Users\chris\Documents\ChatGPT\SmartBetSports\nbastat\predictions.db`

Do NOT use the older Documents\Codex copy. Stage the incoming file on this laptop as:

`C:\Users\chris\Downloads\predictions.home-clean.db`

Expected complete-file SHA-256:

```text
057efc76966dbeda3efb45569111a6e9e185da797230f91766b62e4fc0c9e33c
```

Only after the transfer is separately authorized, verify the incoming file, preserve the laptop DB, and copy:

```powershell
cd C:\Users\chris\Desktop\nbastats\nbastat
$incomingDb = 'C:\Users\chris\Downloads\predictions.home-clean.db'
$expectedDbHash = '057efc76966dbeda3efb45569111a6e9e185da797230f91766b62e4fc0c9e33c'
if ((Get-FileHash -LiteralPath $incomingDb -Algorithm SHA256).Hash -ine $expectedDbHash) { throw 'Incoming database hash mismatch' }
if ((Test-Path .\predictions.db-wal) -or (Test-Path .\predictions.db-shm)) { throw 'Stop SQLite writers and checkpoint before copying' }
$priorDbBackup = '.\predictions.before-home-import.' + [guid]::NewGuid().ToString('N') + '.db'
Copy-Item -LiteralPath .\predictions.db -Destination $priorDbBackup -ErrorAction Stop
Copy-Item -LiteralPath $incomingDb -Destination .\predictions.db -ErrorAction Stop
if ((Get-FileHash .\predictions.db -Algorithm SHA256).Hash -ine $expectedDbHash) { throw 'Copied database hash mismatch' }
git check-ignore predictions.db
git ls-files -- predictions.db
```

Then verify the stored frozen JSON using a read-only SQLite connection (no schema initializers or grading):

```powershell
py -3.14 -c "import sqlite3,hashlib; c=sqlite3.connect('file:predictions.db?mode=ro',uri=True); rows=c.execute('SELECT prediction_json FROM nfl_game_predictions WHERE user_id=0 AND season=2026 AND season_type=? AND display_week=3 ORDER BY game_id',('preseason',)).fetchall(); digest=hashlib.sha256(''.join(row[0] for row in rows).encode('utf-8')).hexdigest(); print('Frozen predictions:',len(rows)); print('Baseline SHA-256:',digest); assert len(rows)==16 and digest=='a8a405ba262ef59bedb1b7bfcdf1ca4a7c0bf78cafe4b0ff0c1268b7d8b2412a'; c.close()"
```

## Push only the integration branch

```powershell
git push -u origin integration/smartbets-laptop-home-sync
```

No force push, main merge, branch deletion or tag deletion is needed.

## Files changed relative to the protected laptop HEAD

- `.gitignore`
- `.vercelignore`
- `backend/app/api/auth.py`
- `backend/app/database.py`
- `backend/app/main.py`
- `backend/app/services/auth_service.py`
- `backend/app/services/entitlement_service.py`
- `backend/app/services/nfl_experiment_service.py`
- `backend/app/services/nfl_product_service.py`
- `backtesting/__init__.py`
- `backtesting/prediction_store.py`
- `config.py`
- `conftest.py`
- `data/nfl_roster_2026.json`
- `data/nfl_team_game_history.json`
- `database_safety.py`
- `docs/nfl-experiment-evaluation.md`
- `docs/nfl-personnel-provider-plan.md`
- `docs/regular-season-week1-readiness.md`
- `docs/smartbets-integration-handoff.md`
- `frontend/__tests__/nfl-game-breakdown.test.js`
- `frontend/__tests__/ui-regression.test.js`
- `frontend/app/analyze/classic/page.jsx`
- `frontend/app/analyze/page.jsx`
- `frontend/app/dashboard/page.jsx`
- `frontend/app/fantasy/page.jsx`
- `frontend/app/games/[slug]/page.jsx`
- `frontend/app/games/page.jsx`
- `frontend/app/history/page.jsx`
- `frontend/app/internal/experiments/week3/page.jsx`
- `frontend/app/parlays/page.jsx`
- `frontend/app/performance/page.jsx`
- `frontend/components/auth/AuthProvider.jsx`
- `frontend/components/games/NflMatchup.jsx`
- `frontend/components/layout/MobileNav.jsx`
- `frontend/components/layout/PremiumNavbar.jsx`
- `frontend/components/teams/TeamLogo.jsx`
- `frontend/lib/api.js`
- `frontend/next.config.mjs`
- `nfl_data_service.py`
- `nfl_parlay_builder.py`
- `nfl_parlay_grader.py`
- `nfl_performance_report.py`
- `prediction_storage.py`
- `tests/test_analyze_information_architecture.py`
- `tests/test_auth_account_deletion.py`
- `tests/test_billing.py`
- `tests/test_database_isolation.py`
- `tests/test_multi_sport_nfl.py`
- `tests/test_nfl_experiment_service.py`
- `tests/test_nfl_product_service.py`
- `tests/test_smartbets_integration.py`
- `tools/build_nfl_product_data.py`
