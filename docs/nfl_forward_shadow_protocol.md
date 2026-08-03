# NFL player-prop forward-shadow protocol

The System A price policy is now frozen for forward shadow observation. This is not authorization to wager. The frozen configuration is the `residual_half_life_9` policy with a minimum expected value of 5%, selected from the eight periods preceding the 2025 Week 18 test fold. The Week 18 outcome was not available when that fold selected the configuration and threshold.

## Workflow

1. Generate price-blind System A probabilities from pregame data.
2. Apply the frozen prior-fold calibration and residual configuration.
3. Supply a complete OVER/UNDER pair with a verified pregame execution price for each exact line.
4. Lock the batch no later than the prediction cutoff.
5. The ledger records one BET or PASS decision per player/game/market/line and preserves both candidate sides.
6. After games complete, write grades to a separate artifact. Locked entries are never edited.
7. Aggregate forward results without changing the policy until the preregistered evidence window is complete.

The ledger rejects incomplete pairs, duplicate keys, post-cutoff quotes or predictions, locks after cutoff, candidates containing outcomes or grades, invalid probabilities, changed decisions for an existing base, and attempts to overwrite a grade artifact with different outcomes.

The local repository has no 2026 NFL snapshot directory yet, so the shadow ledger is initialized with zero decisions. This is the correct state until legitimate pregame 2026 data is captured. Historical 2024–2025 records must never be relabeled as forward shadow evidence.

The frozen prediction bridge fits the selected distribution families on completed 2023–2025 System A history, applies the frozen market/side calibration methods, and then fits the nine-period recency-weighted market residual on completed priced history. Future generation requires a season later than every training season. It rejects completed games, outcome-bearing quotes, late quotes, late generation, incomplete exact-line book pairs, missing canonical player identities, and invalid prices. Consensus no-vig probability remains separate from the best executable side price.

The operational command is `python -m backtesting.run_nfl_system_a_shadow`. Its `preflight` action is offline and reports whether a requested week has both `games.json` and `player_prop_odds.json`. `prepare` generates a timestamp-addressed immutable candidate artifact and locks it into the ledger; rerunning identical inputs is idempotent. `grade` waits for `player_stats.json`, writes separate content-addressed grades for every matching batch, and refreshes the derived summary without editing a decision.

Snapshot acquisition remains a separate, explicitly authorized operation. `python -m backtesting.build_nfl_season --season 2026 --start-week 1 --end-week 1 --plan` may be used to inspect the free preparation and exact paid-request plan. The plan mode does not make paid Odds API requests. A paid fetch must never be inferred merely because the shadow workflow is waiting.

The 2026 Week 1 safe plan discovered 16 scheduled games through the free ESPN preparation path. The repository now has `games.json`, but no `player_prop_odds.json`. The season builder also displayed a 180-credit estimate for six grouped historical team-market requests; those h2h/spread/total requests do not satisfy the player-prop shadow input and were intentionally not executed. The Week 1 shadow preflight therefore remains `WAITING_FOR_PREGAME_DATA` with zero paid credits used.

## Live player-prop capture

Use `python -m backtesting.capture_nfl_live_player_props plan` to inspect a week without network access or API credits. The default capture window is 72 hours before kickoff. A game is eligible only when it is still pregame, inside that window, and its canonical `player_identities.json` records are already present.

If identities are missing, `python -m backtesting.historical_roster_acquisition --season 2026 --week WEEK --snapshot-root backtesting/data/snapshots --cache-root backtesting/data/raw_cache --allow-network` may capture the then-current ESPN team rosters at no Odds API credit cost. Run it before kickoff so its capture time is valid contemporaneous evidence. Then run `python -m backtesting.capture_nfl_live_player_props identities --snapshot-root backtesting/data/snapshots --season 2026 --week WEEK` to materialize the canonical registry from local evidence without network access. The live planner can also derive readiness from the roster artifact without modifying files.

The launch capture requests only `player_receptions`, `player_reception_yds`, and `player_rush_yds` in the US region. The worst-case ceiling is therefore three credits per event. Live capture requires both `--allow-paid-fetch` and an explicit `--max-paid-credits` large enough for every ready event. Free event discovery happens only after identity readiness and authorization pass. The command stops before any request that could exceed the ceiling, records the provider's safe quota headers, removes incomplete OVER/UNDER pairs, and never persists post-cutoff or unresolved-player quotes. Empty results remain diagnostic and cannot erase an existing valid snapshot.

Player props are expected to appear only as kickoff approaches. A `WAIT_OUTSIDE_CAPTURE_WINDOW`, `IDENTITIES_MISSING`, or `NO_COMPLETE_QUOTES` result is not a model failure and does not authorize widening the market list or spending additional credits. Capture is data acquisition for shadow evaluation only; it never authorizes a production wager.
