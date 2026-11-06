# SmartBetSports NBA algorithm diagnosis and model research

Date: 2026-10-29  
Status: research-only; no production model promotion; no wagering authorization

## Executive verdict

The production Elo formula is mathematically correct and current-season finals are reaching it. The 100-home / 2-away split is therefore **not a stale-ingestion or team-mapping failure**. It is the expected early-season behavior of a same-season-only Elo model that resets every team to 1500 and then adds a fixed 65 Elo points to the home team.

At equal ratings the formula assigns the home team a 59.25% win probability. An away team can be selected only when its rating exceeds the home rating by more than 65 points. After 68 completed 2026–27 games, all 30 ratings remain in a narrow 1460.86–1549.25 range. Only DET at BKN and POR at UTAH cross the away threshold, and both do so by less than three adjusted Elo points.

The current Elo is materially better than an always-home rule historically, but it is not the strongest evaluated method. On the untouched 2025–26 test season, the existing form/rest logistic candidate improved Brier score and log loss with independently reproduced results. A newly evaluated hybrid produced the best numerical test metrics, but its incremental advantage over the existing candidate is small and its prior-season carryover state is not yet available from the current-season-only cloud store. The safest next model step is therefore continued prospective shadow evaluation of `nba-game-logit-form-rest-research-v2`, not production promotion.

## A. Elo root-cause analysis

### Exact implementation

- Initial rating: 1500 for every team at the start of each season.
- Previous-season carryover: none.
- Regression toward the mean: none because no previous ratings are carried.
- K-factor: 20.
- Home-court adjustment: +65 Elo points.
- Margin-of-victory adjustment: none.
- Probability: `1 / (1 + 10^(-((home Elo + 65) - away Elo) / 400))`.
- Rating updates: chronological verified regular-season finals only.
- Inputs: final scores and team identities; no box-score statistics, rest, injuries, lineups, rosters, or market odds.
- Forecast lifecycle: upcoming baseline forecasts are recomputed after ingestion and stored separately from frozen official predictions.

The probability conversion and zero-sum rating update are correct. No sign error, home/away inversion, duplicate team identifier, or chronological leakage was found.

### Why 100 of 102 are home picks

The decision boundary is:

`home Elo + 65 >= away Elo`

Equivalently, the away team must lead by more than 65 Elo points. The current rating spread is only 88.39 points from best to worst. Most matchups therefore remain on the home side of the boundary even when the away team has a modest rating advantage.

The two away selections are:

| Game | Away Elo | Home Elo | Adjusted home difference | Away probability |
|---|---:|---:|---:|---:|
| DET at BKN | 1529.53 | 1461.71 | -2.81 | 50.40% |
| POR at UTAH | 1531.62 | 1466.53 | -0.09 | 50.01% |

This is an expected consequence of the current configuration and a model-design limitation, not evidence that a balanced home/away split should be forced.

### Issue classification

| Finding | Classification | Disposition |
|---|---|---|
| Elo probability and update math | Expected/correct behavior | No mathematical change |
| 100/2 selection split | Expected early-season behavior plus model-design limitation | Explain and monitor; do not force balance |
| No previous-season prior | Model-design limitation | Evaluate only in a versioned challenger |
| Fixed 65-point home adjustment with compressed early ratings | Configuration limitation, not proven defect | Enhanced Elo test selected 35 on validation; do not change production in place |
| No margin, form, or rest inputs | Model-design limitation | Existing candidate addresses these with pregame-only features |
| Shadow candidate created only one frozen snapshot and could not refresh latest forecasts | Implementation defect in the research-shadow lifecycle | Repaired in staging code; official snapshot remains immutable |
| Shadow feature cutoff equaled generation time rather than observed evidence time | Metadata/data-lineage defect | Repaired in staging code |
| Historical shooting, lineup, injury, and market features unavailable | Data-quality/coverage gap | Do not claim those models were evaluated |

## B. Historical model comparison

Frozen split: 2023–24 training; 2024–25 validation and model selection; 2025–26 untouched test. Every feature is emitted before the current game's score is applied.

| Model | Accuracy | Brier | Log loss | AUC | ECE | Home pick % | Games |
|---|---:|---:|---:|---:|---:|---:|---:|
| Always home | 55.48% | 0.24712 | 0.68738 | 0.5000 | 0.01137 | 100.0% | 1,231 |
| Current Elo | 66.13% | 0.21218 | 0.61264 | 0.7286 | 0.05747 | 74.7% | 1,231 |
| Verified/corrected Elo | 66.13% | 0.21218 | 0.61264 | 0.7286 | 0.05747 | 74.7% | 1,231 |
| Enhanced Elo | 69.21% | 0.20467 | 0.59606 | 0.7395 | 0.03106 | 57.7% | 1,231 |
| Score/form gradient boost | 66.53% | 0.20729 | 0.60180 | 0.7321 | 0.02109 | 58.7% | 1,231 |
| Existing form/rest logistic candidate | 68.56% | 0.20461 | 0.59573 | 0.7404 | 0.02300 | 59.8% | 1,231 |
| Elo + logistic hybrid | **69.37%** | **0.20328** | **0.59284** | **0.7449** | 0.03754 | 60.4% | 1,231 |

The current Elo exceeds always-home accuracy by 10.65 percentage points on identical games, so its historical usefulness is not reducible to choosing the home team.

Against current Elo, the paired calendar-week bootstrap estimated:

- Existing candidate: +2.43 percentage points accuracy, +0.00748 Brier improvement, and +0.01670 log-loss improvement. The accuracy interval touches zero, while Brier and log-loss intervals remain positive.
- Enhanced Elo: +3.08 percentage points accuracy (95% CI +0.49 to +5.59), +0.00743 Brier improvement, and +0.01642 log-loss improvement.
- Hybrid: +3.24 percentage points accuracy (95% CI +0.80 to +5.68), +0.00881 Brier improvement, and +0.01960 log-loss improvement.

The hybrid's improvement over the existing candidate is only +0.81 percentage points accuracy and 0.00133 Brier on the single untouched test season. That is promising, but not enough by itself to justify the added prior-season-state dependency.

### Enhanced Elo configuration selected on validation

- Home advantage: 35 Elo points.
- K-factor: 10.
- Previous-season retention after mean regression: 50%.
- Margin-of-victory multiplier: enabled.

These parameters are an exploratory challenger configuration. They are not a repair to the live model and were not applied to production.

### Team-statistics scope limitation

The frozen dataset contains game identifiers, timestamps, teams, final scores, and provider metadata. It does not contain leakage-safe historical offensive rating, defensive rating, pace, effective field-goal percentage, turnover rate, rebound percentages, free-throw rate, lineups, or injuries. The evaluated gradient boost therefore uses the same pregame score/form/rest family as the logistic candidate. It is not represented as the full requested box-score model.

## C. Current 2026–27 diagnosis

- Schedule population reproduced: 237 provider records.
- Regular-season finals: 68.
- Upcoming forecasts: 102.
- Teams mapped: 30.
- Current Elo selections: 100 home, 2 away.
- Existing research candidate on the same cutoff: 68 home, 34 away.
- Latest final in the frozen production cutoff: LAL 103 at LAC 87, game `espn-401909892`.
- Forecast generation reference: 2026-10-29 20:53:34 UTC.
- Feature-data cutoff: 2026-10-29 10:29:40 UTC.

The different 68/34 split from the research candidate is not evidence of superior current-season accuracy; those games have not happened. It only demonstrates that form, margin, and rest features create more team separation than the current Elo.

All 30 ratings and last completed-game updates are in `data/current-team-ratings.csv`. Every one of the 102 baseline forecasts, including rating differential, +65 home adjustment, probability, selection, and cutoff, is in `data/upcoming-forecast-diagnostics.csv`.

## D. Recommended algorithm

### Production decision

Keep `nba-game-elo-baseline-v1` unchanged as the displayed research baseline until a separate promotion decision. It is not broken, and changing its constants in place would destroy version clarity.

### Shadow decision

Continue `nba-game-logit-form-rest-research-v2` as the primary operational shadow candidate. It has:

- exact independent reproduction;
- better held-out Brier, log loss, calibration, AUC, and accuracy than current Elo;
- current data inputs already available in the cloud ingestion path;
- a frozen artifact with an integrity hash;
- lower operational complexity than the hybrid.

Treat `nba-game-elo-logit-hybrid-research-v1` as the highest-scoring exploratory method, not yet the recommended operational shadow. Before it can run faithfully, SmartBetSports needs a versioned prior-season Elo state artifact or historical-season state in the cloud store. It also needs a second untouched season or prospective evidence to verify that its small incremental gain persists.

## E. Staging implementation

The research-shadow lifecycle was repaired without changing production Elo math:

1. A shadow official prediction is still frozen once and remains immutable.
2. New `LATEST_FORECAST` shadow snapshots are appended when evidence or probability changes.
3. Repeated runs with unchanged evidence are idempotent.
4. The feature cutoff now records the latest observed evidence retrieval time rather than the generation time.
5. The model remains `RESEARCH_ONLY`, cannot authorize wagers, and is excluded from customer-facing baseline reads.

Focused validation: 54 NBA model/lifecycle and NFL V2 safety tests passed, including immutable official snapshots, refreshed latest forecasts, idempotency, independent model separation, and leakage checks. The established application suite recorded 921 passes and one skip; its only failure was an unrelated pre-existing frontend information-architecture assertion for a missing `Fantasy Football` label.

### Controlled cloud staging validation — 2026-11-06

The repair was also deployed to a Vercel Preview build and exercised against the positively identified `SmartBetSports Staging` Supabase project. Before any write, the endpoint reported environment `staging`, database role `smartbets_staging_app`, and the exact pre-run official-prediction checkpoint of 219 rows with digest `d5394ceea24e28931d4ff855a1421cd6`.

The controlled catch-up run `nba-auto-09bcbe5164a24f469f71857e1f4314c2` completed successfully from 2026-11-06 21:19:03 UTC through 21:20:42 UTC:

- 293 schedule records fetched and 172 inserted or changed.
- 123 current-season regular-season finals were present after catch-up; the latest eligible final was retrieved at 2026-11-06 21:19:52 UTC.
- 103 upcoming baseline forecasts and 103 research-candidate forecasts were appended as `LATEST_FORECAST` snapshots.
- 85 new official baseline snapshots and 85 new official candidate snapshots were frozen for previously unseen upcoming games.
- All 219 pre-existing prediction snapshots retained the exact pre-run aggregate hash `dbef6979e1b38f1524733692dfec2609`.
- All 9 pre-existing settlements retained the exact pre-run aggregate hash `28c9c6d5a12fa43a16aebe12950dfa36`.
- Feature-cutoff-after-generation violations: 0.
- Official predictions generated at or after tipoff: 0.

With 123 finals incorporated, the unchanged Elo selected 86 home teams and 17 away teams across the 103 upcoming games. The existing form/rest candidate selected 66 home and 37 away. This forward staging result supports the root-cause diagnosis: the extreme 100/2 split relaxed as same-season ratings separated, without changing Elo math or forcing a target home/away balance.

This was a successful **controlled** cloud execution, not an unattended staging Cron execution. No staging NBA data-refresh Cron was installed because Vercel Preview remains protected; the production NBA pipeline and production deployment were not modified. The production API remained healthy after the staging run.

No production model, production schedule, historical prediction, grading record, NFL V2 logic, parlay system, player-prop system, or frontend was changed.

## F. Production integration plan

No promotion is recommended now. The existing daily NBA cloud cycle can safely continue to:

1. ingest and validate finals;
2. update current-season chronological ratings and form histories;
3. append a new research-only latest forecast when inputs change;
4. preserve the original official shadow snapshot for prospective grading;
5. compare the candidate and baseline on identical completed games.

Before any serving change, require a reviewed model-registry action, a prospective sample, calibration review, full backend regression, rollback verification, and explicit founder approval.

## G. Final verdict

1. **Was the original Elo algorithm broken?** No. Its core math, update direction, ordering, and team mapping are correct. A separate research-shadow refresh defect was fixed in staging code.
2. **Why almost all home teams?** Same-season ratings remain compressed, while a fixed +65 home adjustment means an away team needs a greater than 65-point Elo lead.
3. **Are the probabilities correct?** Yes for the implemented formula. Correct arithmetic does not imply optimal calibration or model design.
4. **Which model performs best?** The hybrid has the best single held-out test metrics. The existing logistic candidate is the strongest operationally compatible shadow candidate.
5. **How much improvement is demonstrated?** Hybrid versus current Elo: +3.25 percentage points accuracy, -0.00889 Brier, and -0.01980 log loss on 1,231 untouched games. Existing candidate versus current Elo: +2.44 points accuracy, -0.00757 Brier, and -0.01691 log loss.
6. **Does it persist across seasons?** Current Elo is weaker than the selected challengers on both the 2024–25 validation season and 2025–26 test season, but only 2025–26 is untouched. More prospective seasons are required for a strong persistence claim.
7. **Can a candidate run automatically with fresh data?** Yes for the existing logistic candidate; its inputs are already produced by the daily pipeline. The hybrid needs prior-season state operationalized first.
8. **What remains before replacement?** Prospective immutable shadow results, a larger forward sample, a second independent period, calibration monitoring, verified market comparisons where lawful odds exist, operational rollback validation, and explicit approval.

## Prioritized next actions

1. Run the repaired existing candidate as a research-only daily shadow and accumulate at least 300–500 prospectively frozen games.
2. Add model-monitoring views for paired baseline/candidate Brier, log loss, calibration, disagreement outcomes, and feature freshness.
3. Design a versioned prior-season Elo state artifact, then re-evaluate the hybrid without changing production serving.
4. Build a leakage-safe historical team box-score feature store before claiming a full offensive/defensive statistics model has been tested.
5. Revisit promotion only after prospective results, rollback rehearsal, and explicit founder approval.

## Reproducibility and source receipt

- Historical dataset: `../nba-model-quality-2026-10-25/nba-history-2023-2026.json`.
- Dataset SHA-256: `fdb622679018aba6aca61f6c32b31f287bab807c63451dd931a548bfaaafad47`.
- Historical rows: 3,693; 1,231 in each season.
- Duplicate game IDs: 0; missing scores: 0; ties: 0; feature-cutoff violations: 0.
- Analysis command: `python -m backtesting.nba_algorithm_diagnosis --dataset artifacts/nba-model-quality-2026-10-25/nba-history-2023-2026.json --output-dir artifacts/nba-algorithm-research-2026-10-29/data`.
- Independent reproduction: `python -m backtesting.reproduce_nba_model_quality --dataset ... --reference ...` as listed in `README.md`.
- Provider for bounded current-season schedule reconstruction: the repository's existing ESPN scoreboard adapter; primary monthly feed returned HTTP 403 and the already-implemented dated CDN fallback succeeded.

This report does not evaluate betting profitability because verified historical sportsbook prices were not present in the reviewed dataset.
