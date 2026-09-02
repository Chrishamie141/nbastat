# NFL 2026 Week 1: forward experiment readiness

## Current verified preparation (2026-09-02)

- Experiment: **NFL-2026-REG1-v1**, regular season only.
- Isolated store: `backtesting/market_capture/nfl_2026_reg1_v1.db` (ignored/untracked).
- Official ESPN schedule: **16 unique games**; all mapped before freezing.
- Frozen baseline predictions: **16/16**, generated before T−60m deadlines.
- Model: unchanged `nfl_game_baseline_v3`; no Week 3-based tuning.
- Frozen manifest hash: `ee7caccb4ab4eb920157b359b89d5cc5cad5af6a6cbd7228cf1f13f0fa1e4261`.
- Prediction-set hash: `e57e7e557a19c584dfabc097489bd39b1ea0550180c36f41bc6abe4c8d2591e9`.
- One authorized live Odds API preflight: **HEALTHY**, authentication PASS, **16/16**
  matched games, provider timestamp `2026-09-02T16:13:24Z`, headers reported **3 used /
  99,997 remaining** credits. These are observed values, not future quota guarantees.
- Market snapshots: **0**, with **48 future checkpoints** pending. The preflight records
  diagnostics only and does not backfill market observations outside their windows.
- Regular-season grades and qualified wagers: **0**; accuracy/units/ROI are not yet measured.
- Overall readiness: **WARN** until the worker is operating, valid prices arrive, and
  results exist. Source-data/model limitations are not resolved by freezing predictions.

Week 3 remains 16 frozen predictions, 16 grades, 11–4–1, three historical market
observations and zero qualified wagers. Its authoritative database hash remains
`3dd28869aafbc84b354316f4df2c026ed8d0e94d0ba5fa6d7e33301866290850`.

## Architecture

`backtesting/nfl_week_workflow.py` extends the existing isolated capture database. It
never opens the default production database. Market creation/identity/timestamp/budget
logic is reused from `capture_nfl_game_markets`; ESPN discovery uses the existing mapping
adapter without production refresh-log writes.

New tables in the isolated file only:

- `forward_predictions`: experiment ID, model version/code hash, winner, probability,
  generation time, frozen input/projection JSON, and per-prediction integrity hash.
- `forward_wagers`: one immutable selection per game/profile/market, price, line,
  probability, edge, creation time and captured-price provenance.
- `forward_finals`, `forward_grades`, `forward_wager_grades`: append-only final outcomes
  and separate prediction/wager grades.
- `forward_events`: mapping verification and preflight audit records.

Triggers enforce pregame prediction/wager inserts and reject evidence updates/deletions.
Repeated freezes skip existing predictions, never regenerate them. Final-score corrections
are blocked for explicit review rather than silently changing historical results.

The model fingerprint hashes the predictor/config implementation files. Input snapshots
retain eligible historical rows and source hashes. Rows unavailable at generation time
are excluded, even if they would have become available before kickoff. The reused model
also applies its historical pregame eligibility rules. No model parameters are fitted or
changed by this workflow.

The frozen schedule is checked by ID, home/away and kickoff against ESPN before freezing
and result ingestion. Provider kickoff changes cause a fail-closed review requirement,
not silent rescheduling/regeneration. The capture layer separately rejects changed Odds
API kickoffs. Invalid/missing predictions remain explicit gaps.

## Price qualification and grading

SAFE/BALANCED/AGGRESSIVE thresholds are copied into each frozen prediction's provenance.
The qualifier reuses those frozen thresholds and the baseline projection for moneyline,
spread and total probabilities. It requires a validated paired market, a current
pregame cutoff and a retrieval no more than 15 minutes old. Model probability must clear
the profile threshold and model-minus-no-vig market probability must clear its edge rule.

The first qualifying selection for each game/profile/market is frozen. No missing-price
wager is created, and no wager can be generated after kickoff from old or newly fetched
prices. Wagers here are **research records**, not sportsbook orders; no betting account or
real money is accessed. Each qualified wager uses one flat research unit when graded.

Postgame ingestion fetches official ESPN finals and grades frozen predictions and already
existing qualified wagers separately. Ties and exact spread/total matches are pushes.
Predictions and wagers are never created during grading. Profile metrics are independent;
do not sum the three profiles as though they were independent portfolios.

## Commands

The Week 1 file already exists. **Do not rerun prepare or replace it.** Inspect it:

```powershell
cd C:\Users\chris\Desktop\nbastats\nbastat
py -3.14 -m backtesting.nfl_week_workflow report --db backtesting/market_capture/nfl_2026_reg1_v1.db
```

Start the foreground market/qualification/final-grading worker after configuring the
Odds API key in the environment:

```powershell
py -3.14 -m backtesting.nfl_week_workflow watch --db backtesting/market_capture/nfl_2026_reg1_v1.db --allow-paid
```

The worker checks locally each minute, but paid requests remain checkpoint-gated and
budgeted. ESPN requests use the existing short-lived provider cache. No operating-system
service is installed automatically. Keep the authoritative machine awake and online or
explicitly deploy this worker with its durable files to an always-on host.

Use `tick` instead of `watch` for a single bounded execution. Read weekly and cumulative
regular-season metrics without modifying the database:

```powershell
py -3.14 -m backtesting.nfl_week_workflow metrics --db backtesting/market_capture/nfl_2026_reg1_v1.db
py -3.14 -m backtesting.nfl_week_workflow season-report --db backtesting/market_capture/nfl_2026_reg1_v1.db
```

Future weeks add `--include-db <another-regular-season-experiment.db>` to `season-report`.
Different seasons and overlapping slates are rejected to prevent double counting.
Cumulative output includes winner record, accuracy excluding pushes, calibration buckets,
coverage, profile-specific wager counts/records/units/ROI and small-sample warnings. No
preseason rows enter regular-season totals.

For a genuinely new week, use a new filename:

```powershell
py -3.14 -m backtesting.nfl_week_workflow prepare --db backtesting/market_capture/nfl_2026_reg2_v1.db --season 2026 --week 2 --credits 90
```

`freeze` may fill genuinely missing predictions **only before their deadlines**, checking
the official mapping again; it never changes existing frozen predictions.

## Preflight and market budget

The one-shot provider preflight was already run for Week 1. This command is idempotently
blocked from a second paid request in the same experiment:

```powershell
py -3.14 -m backtesting.nfl_week_workflow preflight --db backtesting/market_capture/nfl_2026_reg1_v1.db --allow-paid
```

It reserves three credits inside the same 90-credit ceiling before making one league-wide
request. The remaining local ceiling is 87 credits after this preflight. The account's
remaining quota can also be consumed by other jobs; it is not an account-wide spending lock.
Preflight stores source timestamp, game mapping, authentication and usage diagnostics;
it creates no market observations or wagers. Capture checkpoints remain T−24h, T−6h and
T−60m with the documented late tolerances in [the capture guide](nfl-market-capture-workflow.md).

## Marketing feed and readiness checks

After configuring/initializing the separate social store and signing key, the worker can
push verified aggregates hourly using `--sync-social`. It does **not** publish to X:

```powershell
py -3.14 -m backtesting.nfl_week_workflow watch --db backtesting/market_capture/nfl_2026_reg1_v1.db --allow-paid --sync-social
```

An unavailable social feed is reported as `SOURCE_SYNC_BLOCKED` and must not interrupt
market capture. Schedule network outages are reported without inventing final results;
integrity/schedule-change errors stop for operator review. Hosted X posting remains gated
by fresh signed evidence and explicit publishing switches; see [social-marketing.md](social-marketing.md).

`report` emits requirement-by-requirement PASS/WARN/FAIL for independent scope, mapping,
freeze/provenance, no-overwrite behavior, market integration/checkpoints, provider auth and
mapping, strict cutoff, three markets, pregame qualification, no retroactive wagers, grading,
separate metrics, coverage, worker operation and sample sufficiency. PASS on a code-path
capability is not proof that an unattended worker has been deployed; worker operation
remains WARN until operationally verified. Successful preflight does not imply market
coverage or future profitability.

## Implementation validation

On 2026-09-02, the full backend run completed with **670 passed, 2 skipped**
(unavailable PostgreSQL driver and opt-in live NFL provider). Focused lifecycle/social/
capture/isolation checks passed **91 tests**, with the PostgreSQL-driver check skipped.
Frontend **23 tests**, lint, production build and `git diff --check` passed. Week 3's
entire database hash and Week 1's frozen prediction-set hash remained unchanged through
validation. No X post was made. The one explicitly authorized Odds API preflight used
three credits; mocked tests consumed none.

The new experiment database is intentionally ignored by Git. Back it up separately with
its SHA-256 before moving machines; pushing source code does not transfer frozen evidence.
