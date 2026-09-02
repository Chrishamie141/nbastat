# NFL experiment evaluation policy

## Integrity

Each experiment registers an expected SHA-256 for the exact system-owned
`prediction_json` strings ordered by game ID. Official grading recomputes this
hash first and fails closed on any mismatch. Outcomes, schedule changes,
markets, and grades are stored separately from the frozen prediction record.

The 2026 preseason Week 3 baseline is:

`a8a405ba262ef59bedb1b7bfcdf1ca4a7c0bf78cafe4b0ff0c1268b7d8b2412a`

## Prediction and wager records

Prediction performance grades every frozen winner prediction that receives a
verified final score. It does not require a sportsbook price.

Wagering performance includes only a selection that met the experiment's
profile probability and edge rules at a verified market observation strictly
before kickoff. A correct winner lean without a qualifying market remains
`NO_BET` and is excluded from wager record, units, and ROI.

## Flat-unit returns

The official evaluation risks one unit on every qualified wager:

- A winning positive American price returns `price / 100` net units.
- A winning negative American price returns `100 / abs(price)` net units.
- A loss is `-1.00` unit.
- A push is `0.00` units.
- `NO_BET` risks no units and is excluded.

ROI is `net units / total flat units risked`.

## Calibration

Reports use fixed probability buckets of 50.00–54.99%, 55.00–59.99%,
60.00–64.99%, and 65.00%+. A sample below 30 graded predictions is labeled
`INSUFFICIENT_SAMPLE`; the 16-game Week 3 experiment cannot establish model
calibration by itself.
