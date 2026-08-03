# System A calibrated price-policy research

The price-policy layer joins both sides of each leakage-safe calibrated distribution forecast to the existing frozen 2024 and 2025 historical evaluation prices. Probability fitting never sees sportsbook prices. After both sides are calibrated and normalized to a coherent pair, price enters only through expected value:

`EV = win_probability × (decimal_odds - 1) - loss_probability`

For each player, game, market, and line, the policy selects at most one side. It bets only if that side clears an EV threshold chosen from earlier periods; otherwise it passes. Inner selection may choose market-only or a recency-weighted market-plus-model residual. A non-positive inner policy also produces PASS.

## Frozen coverage

- 78,360 calibration-ready side forecasts and 39,180 complete calibrated bases.
- 63,964 sides and 31,982 complete bases matched frozen best-price evaluation rows.
- 31 periods from 2024 Week 6 through 2025 Week 18.
- Three launch markets and eight bookmakers.
- 7,198 otherwise-ready bases per side lacked matching frozen evaluator prices and were excluded symmetrically.

## Untouched result

The nested selected residual probability was marginally better than the no-vig market on probability quality: Brier 0.238714 versus 0.238844, log loss 0.669938 versus 0.670161, and ranking AUC 0.61498 versus 0.61277.

That improvement did not become a profitable policy. The nested policy placed 312 bets across 88 games, lost 7.15 units, returned -2.29% ROI, and had a game-cluster bootstrap 95% ROI interval of -14.04% to +9.47%. It improved the ROI point estimate relative to the V3 fixed-threshold and market-favorite controls, but both paired improvement confidence intervals crossed zero. It also missed the 500-bet sufficiency requirement.

Production promotion remains blocked. These results should not be used to retune a threshold on the same 2024–2025 outcomes. The defensible next evidence is a later untouched forward shadow window, or genuinely new pregame features trained and selected without revisiting these test labels.
