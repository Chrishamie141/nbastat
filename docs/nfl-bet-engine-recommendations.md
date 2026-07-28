# NFL recommendation ticket architecture

The recommendation layer is model-agnostic. It consumes frozen pre-kickoff H2H,
spread, and total candidates; it does not alter V1/V2 projections.

## Winner anchors and objectives

Winner anchors rank H2H selections by `0.55 model win probability + 0.20 market
probability + 0.10 model/market agreement + 0.10 data completeness + 0.05
calibration quality`. Edge and EV remain visible candidate fields but do not dominate
this likelihood ranking. SAFE additionally requires a favorite and its probability
floor, so a high-edge 44% underdog can remain a single without becoming an anchor.

Ticket objectives are intentionally specified a priori:

* SAFE `cash_probability_score = .75 raw joint probability + .25 mean quality`.
* BALANCED `risk_adjusted_value_score = .40 joint + .35 quality + .25 payout score`.
* AGGRESSIVE `upside_score = .25 joint + .30 quality + .45 payout score`.

Construction starts with the strongest eligible leg and tests each subsequent leg
for conflicts, exposure, probability cost, quality, incremental payout/value, and
the policy's soft target payout. It stops when the objective has enough payout or
no candidate improves it; maximum legs is a guardrail, not a target.

## Probability and EV safety

Independent-game tickets retain `raw_joint_probability` as the product baseline.
Only a supplied held-out `ProbabilityCalibrator` can populate
`adjusted_joint_probability` and make EV `calibrated`. Without one, adjusted
probability is null, raw EV is `provisional`, and compounding is warned. Raw EV over
100% adds `extreme_estimated_ev` diagnostics without automatic rejection.

SGPs never multiply correlated legs. Both joint probabilities and both EV fields
are null and status is `unavailable_correlated`. Their recommendation score is a
non-probabilistic blend of script alignment and leg quality.

## SGP scripts and weekly output

Projected scores classify games as favorite-control/close and high/low-scoring,
`underdog_live`, or `uncertain`. Initial coherent pairs include favorite ML plus the
supported total direction and underdog spread plus under; same-team ML plus spread
is rejected as redundant.

`RecommendationSlate` contains ranked `best_singles`, all qualified singles, three
winner slots, per-game SGP slots, best SGP, and three diversified slate slots.
Every strategy slot is either `recommended` with a ticket or `no_bet` with reasons.
Default evaluation artifacts aggregate rejection counts and examples; pass
`--debug-rejections` to retain every audit row.

Offline Weeks 1–6 evaluation (descriptive, never evidence of profitability):

```bash
python -m backtesting.evaluate_nfl_bet_engine --season 2025 --start-week 1 --end-week 6 --models nfl_game_baseline_v1,nfl_game_baseline_v2 --ticket-types singles,winner,sgp,slate --risk-profiles safe,balanced,aggressive --stake 10 --output reports/nfl_bet_engine_w01_w06.json --tickets reports/nfl_bet_engine_w01_w06_tickets.csv --markdown reports/nfl_bet_engine_w01_w06.md
```
