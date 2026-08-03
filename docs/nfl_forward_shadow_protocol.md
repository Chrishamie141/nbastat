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
