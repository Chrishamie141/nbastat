# NFL System A distribution research

The System A accepted ledgers now feed the existing nested expanding walk-forward distribution pipeline for receptions, receiving yards, and rushing yards. Quarantined games never enter model history or test folds. The feature set adds prior-game targets per team dropback, reception rate, receiving yards per target, player rush share, and rushing yards per attempt. Each feature is updated only after its source week is featurized.

The 2024–2025 untouched evaluation produced 18,071 projections. Against the frozen prior distribution model on the same player/game/market rows, the System A candidate changed aggregate MAE by +0.0051 and CRPS by +0.0560. Rushing-yards MAE improved by 0.0076, while receptions and receiving yards regressed slightly. This does not support promotion.

High-confidence calibration also remains unacceptable: the nominal 80% and 85% low-variance groups achieved approximately 57.8% and 57.7% empirical hit rates. The model remains research-only. The next modeling experiment should calibrate distribution probabilities using only prior outer-fold predictions and should compare shrinkage or isotonic calibration without changing the untouched test labels.

Run:

```powershell
python -m backtesting.research_nfl_player_stat_distributions --system-a-dir backtesting/results/nfl_system_a_m0_m1 --output-dir backtesting/results/nfl_player_stat_distributions_system_a_2024_2025
python -m backtesting.compare_nfl_distribution_research --baseline backtesting/results/nfl_player_stat_distributions_2024_2025/player_projection_rows.json --candidate backtesting/results/nfl_player_stat_distributions_system_a_2024_2025/player_projection_rows.json --output backtesting/results/nfl_player_stat_distributions_system_a_2024_2025/paired_system_a_comparison.json
```
