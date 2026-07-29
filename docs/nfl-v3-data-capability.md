# NFL V3 data capability and research policy

## Locked research windows

For the 2025 research cycle, Weeks 1–6 are the **development window** and Weeks
7 onward are the **locked holdout**. Normal evaluation never reads holdout
outcomes. Holdout evaluation requires both `--evaluate-holdout` and a manifest
created before evaluation; the command verifies the exact configuration and
every snapshot hash. Holdout results are reporting-only and must never trigger
automatic parameter changes.

## Available, leakage-safe inputs

| Source | Capability | Provenance rule |
|---|---|---|
| Team game history and scores | offense, defense, recent form, variance, opponent context | completed and known strictly before kickoff |
| Historical odds | consensus moneyline, spread, total, dispersion | captured strictly before kickoff; kept separate from football probability |
| Schedule/venue | home/away split, rest and short-week context | scheduled or completed data available before kickoff |
| Player statistics | incomplete; not enabled in V3 | requires a timestamped historical snapshot audit |

Chronological Elo and richer opponent adjustment are supported by the feature
interface but should only be enabled in experiments with adequate prior history.
Every feature records source timestamps, provenance, availability, missingness,
and any explicit fallback. The score model combines independent offense and
opponent-defense estimates, applies configurable home field, and uses empirical
scoring variance with conservative floors. A normal margin distribution produces
the football-only H2H probability; historical market probability is exposed
separately and blended only by an explicit configuration weight.

## Unavailable or incomplete future sources

Injuries, weather, confirmed starting quarterback, offensive-line availability,
travel, coaching changes, and general player availability do not yet have a
complete timestamped historical source. Their interfaces therefore return
`unavailable` with `historical_source_unavailable`; V3 never fabricates values.

## Calibration, evaluation, and confidence

Platt and isotonic research calibrators accept development observations only.
Development scoring uses chronological folds/walk-forward fitting, never random
shuffle. Configuration selection prioritizes Brier score, log loss, margin MAE,
and total MAE; ROI is secondary. Feature ablations use
`disabled_feature_groups` and report against the same chronological observations.

The legacy `confidence` field is not treated as calibrated certainty: historically
it was largely a deterministic function of sample count. V3 emits no misleading
UI confidence. Future confidence should combine calibrated uncertainty, feature
completeness, model agreement, and calibration quality.

The ticket engine remains unchanged: V3 uses the existing `GameProjection` and
market probability interface consumed by singles, winner parlays, same-game
parlays, and slate parlays.
