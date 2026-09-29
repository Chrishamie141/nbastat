# NFL Week 3 model and parlay diagnostic

Date: 2026-09-27

## Executive finding

The live record is useful descriptive evidence, but it is not a safe tuning
set.  Frozen 2026 predictions must remain unchanged.  The next production
model should be promoted only after a leakage-safe historical evaluation and a
forward shadow period.

The repository currently contains two distinct quality problems:

1. The production game-winner service still uses `nfl_game_baseline_v2`.  It is
   primarily a score-form, venue-split, rest, and Elo model.  It does not have
   verified production features for starting quarterback changes, injuries,
   player availability, coaching changes, offensive-line changes, or weather.
2. The legacy player-prop confidence is a heuristic ranking score, not a
   calibrated probability.  The same-game builder historically multiplied
   these scores even though its own dependency research says that a specific
   pair-dependence model is not available.

## Reproducible evidence

The checked-in 2025 player-prop history contains 18,295 base opportunities and
36,590 side forecasts.  Its season summary reports:

- model hit rate excluding pushes: 50.0%
- model Brier score: 0.4220
- model log loss: 3.3432
- model expected-calibration error: 0.3982
- market Brier score: 0.2477
- market log loss: 0.6885
- market expected-calibration error: 0.0059
- descriptive model ROI: -5.11%

The extreme model-probability buckets are the clearest warning.  Forecasts near
0% and near 100% both resolved near 50%, which is severe overconfidence rather
than useful discrimination.  The direction audit found no evidence that this
was a simple over/under side swap.

Passing yards was the weakest priced segment in the checked-in ROI diagnostic:
47.1% wins and -11.1% ROI, with the 95% clustered interval below zero.  This is
a reason to gate that market in a future policy experiment, not to edit past
tickets.

The V4 prop research candidate improves the baseline materially (Brier 0.3167,
ECE 0.2320, log loss 0.9248), but remains explicitly marked
`RESEARCH_ONLY_PENDING_PROMOTION_CHECK`; its descriptive ROI is still negative.
It must not be silently promoted.

The historical V1/V2 game replay currently produces zero eligible predictions
because the replay snapshots expose zero usable team-history rows.  This means
the production V2 winner model does not yet have a trustworthy full-season
out-of-sample validation artifact in this repository.

## Availability defect fixed in this pass

`get_nfl_player_recent_stats()` always requested ESPN regular-season Week 1.
Weeks 2 and later therefore never entered the live recent-history input, which
caused valid current prop markets to be rejected and made the parlay board look
empty.  The loader now aggregates completed box scores from every week strictly
before the selected matchup week, caches that bounded result, and never reads
the target game's box score.

## Parlay interpretation

A parlay's whole-ticket hit rate must be lower than its component hit rate.  If
three legs each truly hit 52% of the time and were independent, the ticket hit
rate would be about 14.1% (`0.52^3`).  At 70% per leg it would still be only
34.3%.  An 80% whole-ticket target cannot be reached honestly with ordinary
three- or four-leg markets.

For future tickets:

- treat single-leg calibration as the first quality gate;
- keep SAFE tickets to two legs and allow `NO_BET`;
- do not display a joint SGP probability until a pair-specific dependency model
  is validated;
- compare expected value at captured price, not hit rate alone;
- report individual-leg and whole-ticket outcomes separately;
- keep user-created tickets separate from unbiased benchmark tickets.

## Prospective improvement plan

1. Repair the game-model historical replay data contract so V2 and V3 can be
   evaluated on the same 2023-2025 game universe.
2. Train on 2023, validate/freeze on 2024, and use 2025 as untouched test data.
   Compare V2, V3, and a market-consensus baseline on accuracy, Brier score, log
   loss, calibration, and priced ROI.
3. Add verified quarterback/starter, injury/availability, offensive-line,
   weather, travel/rest, and coaching/roster-change features only when their
   timestamped pregame provenance is available.
4. Calibrate winner probabilities.  Do not optimize only straight-up accuracy;
   probability quality determines whether edge and parlay ranking are usable.
5. Promote a prop model market by market.  A strong receptions model should not
   be blocked by—or hide—a weak passing-yards model.
6. Run the frozen challenger in shadow mode for several future weeks before it
   can influence subscriber recommendations.

## Language policy

Customer pages now distinguish concrete states such as `No graded account
predictions yet` and `No prior results at this line`.  Small groups are labeled
`EARLY_SAMPLE` with their exact count.  The label does not pretend every player,
market, line, or probability bucket has enough independent observations merely
because the season has reached Week 3.

## Source artifacts

- `backtesting/results/nfl_player_props_2025_history/season_summary.json`
- `backtesting/results/nfl_player_props_2025_history/systemic_findings.json`
- `backtesting/results/nfl_player_props_2025_history/probability_direction_audit.json`
- `backtesting/results/nfl_player_props_v4_2025_train_2023_2024/v4_summary.json`
- `backtesting/results/nfl_player_props_v4_2025_roi_diagnostics/roi_diagnostics.json`
- `backtesting/results/nfl_player_stat_distributions_system_a_2024_2025/parlay_dependency_diagnostics.json`
