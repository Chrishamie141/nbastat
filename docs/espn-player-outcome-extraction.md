# ESPN completed player outcomes

ESPN event `401772510` exposes Dak Prescott (athlete `2577417`) under the exact
response paths:

* `boxscore.players[team=DAL].statistics[name=passing].athletes[athlete.id=2577417]`
* `boxscore.players[team=DAL].statistics[name=rushing].athletes[athlete.id=2577417]`

The `statistics` object supplies ordered `labels`; each athlete supplies the
corresponding ordered `stats`. The checked regression payload records passing
`21/34`, 188 yards, zero touchdowns, and rushing 4 attempts for 19 yards. It
does not contain a receiving row for Prescott, so extraction does not create
one or turn that missing category into zeros.

Feature repair is cache-only and does not touch odds or predictions:

```bash
python -m backtesting.build_nfl_feature_history \
  --season 2025 --week 1 --game-id espn-401772510 \
  --rebuild-from-cache --validate
```

The command reads the cached scoreboard and summary, replaces only the
selected feature snapshot datasets, and reports category counts, rejected raw
rows, provider-ID coverage, and named Prescott evidence in its diagnostics.
