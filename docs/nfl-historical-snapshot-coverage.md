# NFL historical snapshot coverage

> This report describes data availability, not model performance. Synthetic test fixtures are excluded.

- Requested seasons: 2022, 2023, 2024, 2025
- Available seasons: none
- Missing seasons: 2022, 2023, 2024, 2025
- Weeks discovered: 0
- Games discovered: 0
- Games successfully snapshotted: 0
- Invalid/excluded games: 0

## Dataset coverage

| Dataset | Records | Records / games |
|---|---:|---:|
| outcomes | 0 | 0.00% |
| odds | 0 | 0.00% |
| team_stats | 0 | 0.00% |
| injuries | 0 | 0.00% |
| weather | 0 | 0.00% |
| player_stats | 0 | 0.00% |

## Available weeks

| Season | Week | Games | Valid |
|---:|---:|---:|---:|
| — | — | 0 | 0 |

## Exclusions

- None (no games were discovered).

## Source and timestamp limitations

- ESPN supplies schedules, final outcomes, and completed prior-game history but not auditable historical market quotes.
- The Odds API historical endpoint requires a key and an eligible paid plan; current odds are never substituted.
- No timestamp-verifiable injury, pre-kickoff forecast, or player-history archive is bundled.
- Only records with parseable timestamps strictly before kickoff are eligible inputs.

## Reproduction

```bash
python -m backtesting.build_nfl_historical_snapshots --season 2022 --season 2023 --season 2024 --season 2025
python -m backtesting.validate_snapshots --sport nfl
python -m backtesting.nfl_v1_v2_validation
```
