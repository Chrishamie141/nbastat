# Historical Odds Normalization Report

## 1. Root cause

Historical Odds API responses were parsed with provider-native market keys (`h2h`, `spreads`, `totals`) but the snapshot validator only accepted a separate internal set (`moneyline`, `spread`, `total`, player props). The provider therefore emitted unsupported `spreads`/`totals` values, while `h2h` moneyline rows had no `point`, so `line` was missing. Matching also fell back to the Odds API event id when datetime/team matching failed, producing odds rows whose `game_id` did not exist in the ESPN-derived game snapshot.

## 2. Files changed

- `backtesting/markets.py` adds the shared canonical market enum and aliases.
- `backtesting/game_matching.py` adds reusable `match_game()` diagnostics and team/time matching.
- `nfl_providers.py` uses shared market normalization and game matching when parsing The Odds API events.
- `backtesting/snapshots.py` normalizes odds markets during snapshot import and emits detailed validation diagnostics.
- `tests/fixtures/historical_odds_sample.json` provides a reusable no-live-API historical Odds API payload.
- `tests/test_historical_odds_normalization.py` adds regression coverage for normalization, matching, validation, and replay ingestion.

## 3. Schema before

Odds records required the snapshot fields `game_id`, `market`, `selection`, `line`, `odds`, `sportsbook`, and `captured_at`, but provider output could preserve raw Odds API markets and leave moneyline `line` as `null` because `h2h` outcomes do not include `point`.

## 4. Schema after

Odds records still preserve backward-compatible snapshot fields, and normalized provider rows include stable betting metadata: `game_id`, `event_id`, `commence_time`, `market`, `selection`, `player`, `line`, `odds`, `sportsbook`, `bookmaker`, `captured_at`, `provider`, `source`, `data_as_of`, and `is_pregame`. Moneyline (`h2h`) rows use `line: 0` so required fields do not silently disappear.

## 5. Matching algorithm

`match_game()` returns a diagnostic object with `matched`, `game_id`, `strategy`, and failure `reasons`. It tries provider event ids first when both event and game snapshots expose such ids. It then deterministically checks kickoff datetime within tolerance, home team, away team, and league. It does not rely solely on provider ids.

## 6. Validation improvements

Unsupported or unnormalized market errors now include expected canonical markets, received value, normalization stage, game id, and provider/source. Missing odds field errors now include the missing field, game id, market, and provider/source.

## 7. Test coverage

Regression tests cover a valid historical Odds API payload, `h2h`/`spreads`/`totals` normalization, bookmaker parsing, line parsing, American odds parsing, game matching diagnostics, snapshot validation, and replay ingestion in BETTING and STATISTICAL modes.

## 8. Live API verification

No additional Odds API call was necessary. Existing fixtures and unit tests cover the normalization pipeline.

Ready for final live verification.
