# NFL V2 vs V3 historical evaluation (2023-2025)

## Decision

Do **not** promote V3 to production from this evidence. Across the common
752-game paired universe, V2 and V3 have the same winner accuracy (63.65%),
while V2 has better Brier score and log loss. V3 has lower expected calibration
error, but that improvement is not accompanied by a consistent improvement in
winner selection or score error.

This is model-quality evidence, not a profitability claim. Historical odds are
absent for the 2023 snapshots, so ROI is not computed for the cross-season
winner-model comparison.

## Replay repair

The immutable feature snapshots store completed team-game rows in the week in
which the game occurred. The general replay provider previously read only the
target week's `team_stats.json`; those rows are postgame facts for the target
week and were correctly rejected, leaving zero usable history.

The repaired provider builds a deduplicated chronological view across immutable
weekly snapshots, then exposes only prior-season and earlier-week rows. Per-game
filtering still requires every history timestamp to be strictly before the
prediction cutoff and excludes the target game. Raw snapshots were not edited.

The winner-model evaluator now treats historical odds as optional context. It
does not invent prices, market edges, spread results, totals results, ROI, or
wagers when a snapshot has no odds. Comparative metrics use only the exact
intersection of games predicted by both models.

## Results

| Season | Common games | V2 accuracy | V3 accuracy | V2 Brier | V3 Brier | V2 log loss | V3 log loss | V2 ECE | V3 ECE |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2023 | 208 | 63.94% | 62.02% | 0.2330 | 0.2353 | 0.6588 | 0.6640 | 0.0491 | 0.0482 |
| 2024 | 272 | 64.34% | 66.91% | 0.2212 | 0.2250 | 0.6332 | 0.6417 | 0.1062 | 0.0764 |
| 2025 | 272 | 62.73% | 61.62% | 0.2220 | 0.2310 | 0.6337 | 0.6542 | 0.0604 | 0.0425 |
| Combined | 752 | 63.65% | 63.65% | 0.2247 | 0.2300 | 0.6405 | 0.6524 | 0.0616 | 0.0459 |

There are 751 binary winner outcomes because one game ended tied. In paired
winner decisions, V2 alone was correct on 35 games, V3 alone was correct on 35,
both were correct on 444, and both were wrong on 238. The models therefore show
no net winner-selection advantage in the combined sample.

V3's combined total-score MAE is slightly lower (10.533 vs 10.587), while V2's
combined margin MAE is lower (10.307 vs 10.429). These mixed deltas reinforce
the no-promotion decision.

## Coverage and limitations

- 2023 has no historical odds snapshots. Its evaluation is prediction-only.
- V2 requires four prior games per team. In 2023, which has no 2022 snapshots,
  the common universe begins after the early-season minimum-history period.
- V3 emitted 48 additional 2023 early-season predictions, but they are excluded
  from paired accuracy/calibration metrics because V2 had no comparable output.
- 2024 and 2025 each contain 18 weeks with historical odds; those prices remain
  frozen context and are not retroactively created.
- This pass did not tune either model and did not change production predictions,
  grades, market observations, or frozen experiment artifacts.

## Recommendation

Keep V2 as the production game-winner model. Treat V3's calibration behavior as
a research lead: diagnose why it improves ECE while worsening Brier/log loss,
especially on the 2025 confirmation season. Evaluate V4 player props separately
and do not promote it while its historical ROI remains negative. Any future SGP
or multi-game parlay evaluation must use observed joint outcomes or an explicit
correlation model; multiplying marginal leg probabilities is not valid evidence
of ticket reliability.

## Reproduction

Run each season with the checked-in immutable snapshots:

```powershell
$env:PYTHONPATH='.'
python -m backtesting.evaluate_nfl_v3 --season 2023 --development-start-week 1 --development-end-week 18 --holdout-start-week 19 --models nfl_game_baseline_v2,nfl_game_baseline_v3
```

Repeat for 2024 and 2025. Machine-readable per-game outputs are stored in
`backtesting/results/nfl_<season>_v2_vs_v3.json`.
