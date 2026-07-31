# NFL player-prop evaluation and calibration

Run the evaluator entirely from immutable local snapshots:

```bash
python -m backtesting.evaluate_nfl_player_props --season 2025 --start-week 1 \
  --end-week 1 --snapshot-root backtesting/snapshots --output-dir reports/nfl-props-2025-w1
```

Optional `--market` and `--bookmaker` filters apply before evaluation. The
command contains no provider client and makes no network request. It reads
`games.json`, `player_prop_odds.json`, outcome player rows, and frozen model
probabilities. Probabilities may be embedded in prop quotes or stored in
`player_prop_predictions.json`, keyed by canonical game, player, market, exact
line, and side. Evaluation never recomputes or changes a model formula.

## Units and leakage rules

A **quote** is one bookmaker's side and price. An independent **opportunity** is
`season + week + game_id + canonical_player_id + market + side + exact line`.
For opportunity results, duplicate books collapse to the highest decimal price;
bookmaker, quote timestamp, then canonical JSON provide deterministic ties.
Neither outcome nor grade participates in selection. Quote-level rows remain in
the diagnostic artifact.

An OVER/UNDER pair must have the same game, canonical player, market, exact
line, bookmaker, and historical snapshot timestamp. Raw American implied
probabilities are `100/(A+100)` for positive `A`, and `|A|/(|A|+100)` for
negative `A`. For a complete pair proportional no-vig probability is
`p_side_raw / (p_over_raw + p_under_raw)`. Incomplete pairs are counted and get
no market probability. Model edge is `model probability - no-vig market
probability`; realized outcomes are not an input.

Every quote timestamp must be at or before the game's prediction cutoff.
Canonical identity, finite line, valid nonzero price, supported side, bounded
frozen probability, exact outcome identity, and independently recomputed grade
are mandatory. A violation fails the complete run instead of silently dropping
the row. Missing probabilities are not gradeable model evaluations and remain
reflected by accepted-versus-gradeable counts.

## Metrics and returns

Model and no-vig market probabilities receive Brier score, clipped binary log
loss, and expected calibration error (ECE) over fixed 10-percentage-point bins.
Pushes are excluded from every binary score and observed hit rate, but their
count is reported. ECE is the count-weighted mean absolute difference between
each bin's mean prediction and observed win rate.

All wagers use one flat unit. A win earns `A/100` at positive odds or
`100/|A|` at negative odds; a loss earns -1 and a push earns zero. ROI is net
profit divided by **settled units only**, so pushes do not enter its denominator.
No Kelly sizing, threshold optimization, or strategy selection is performed.
The non-overlapping edge bins are `edge <= 0`, `(0, 2%)`, `[2%, 5%)`,
`[5%, 10%)`, and `[10%, infinity)`.

Wilson 95% intervals describe binary hit rate. ROI uses a deterministic,
fixed-seed, 2,000-draw game-cluster bootstrap so repeated opportunities in one
game are resampled together. This is conservative about within-game dependence,
but a single week is still exploratory. Every group below 30 opportunities is
retained and labelled `INSUFFICIENT_SAMPLE`; that label is not evidence of no
effect.

## Outputs and limitations

The output directory contains deterministic summary, row, calibration, edge,
market, bookmaker and full-breakdown JSON, opportunity CSV, and a SHA-256
manifest. Breakdowns cover market, week, selected bookmaker, side, edge bucket,
market × edge, bookmaker × market, and probability bucket.

`CLV_READY` is false. The canonical schema does not prove that a later quote is
both pregame and a defensible close, so the evaluator does not rename a latest
quote as "closing" or fabricate CLV. Results measure the frozen current model;
they do not tune coefficients, distributions, projections, identities,
acquisition, or betting thresholds, and must not be presented as a profitability
claim from one week.
