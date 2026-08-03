# NFL distribution probability calibration research

The frozen System A distribution forecasts now have a separate leakage-safe calibration layer. Every line is expanded into its OVER and UNDER forecast before calibration. For every outer test week, method selection and fitting use only gradeable forecasts from earlier weeks. An inner trailing-period validation chooses among identity, three fixed shrinkage strengths, and isotonic calibration using Brier score. Current-week outcomes never enter current-week selection or fitting.

Rows produced before the inner validation window has enough evidence remain in full-window metrics but are marked `calibration_ready: false`. They cannot qualify as calibrated high-confidence signals. The layer changes neither player-stat centers nor quantiles and does not use sportsbook prices.

The 2024–2025 run uses the 48,782 gradeable frozen threshold forecasts from the System A distribution study. Advancement requires improvement in full-window Brier and log loss plus at least 500 calibration-ready forecasts at 70% or higher, across at least 15 weeks, with absolute calibration error no greater than five percentage points.

The untouched full-window result across 97,564 side forecasts improved Brier from 0.2571 to 0.2498 and log loss from 0.7469 to 0.7078. There were 78,360 calibration-ready side forecasts across 31 periods. The 632 calibration-ready forecasts at 70% or higher spanned 30 periods, with a 70.25% hit rate against 73.57% average forecast probability. That -3.32 point error passes the five-point research tolerance.

Passing that gate advances the research only to price-aware policy evaluation. Production promotion remains blocked until absolute ROI, its lower confidence bound, drawdown, coverage, and bookmaker concentration are evaluated in untouched walk-forward folds.
