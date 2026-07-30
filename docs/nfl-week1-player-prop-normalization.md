# NFL Week 1 player-prop normalization contract

The rebuild command is strictly cache-only:

```console
python -m backtesting.build_nfl_player_props --season 2025 --start-week 1 --end-week 1 --resume --rebuild-from-cache --validate
```

It refuses to combine `--rebuild-from-cache` with paid fetching. Missing or
invalid cache entries stop the rebuild before persistence. The builder writes
only `player_prop_odds.json` and its manifest entry, using atomic replacement.

## Identity and ordering

A canonical quote is identified by league, season, week, game, canonical player
ID, canonical market, bookmaker, exact line, side, and provider snapshot
timestamp. Thus another line, side, book, or snapshot remains a distinct quote.
Exact copies collapse. If copies with that identity disagree, the copy with the
latest valid market update wins; canonical JSON provides a deterministic
tiebreaker and the conflict remains in diagnostics.

The provider response is normalized, reconciled, checked against the requested
as-of boundary, deduplicated, validated, and only then persisted. A complete
Over/Under pair remains the persistence boundary.

## Historical timestamp semantics

`requested_snapshot_timestamp` is the requested historical as-of boundary.
`provider_snapshot_timestamp` identifies the archive record selected by the
provider. `market_last_update` describes the bookmaker data inside that record.
The provider timestamp is not substituted for the requested leakage boundary.
Both the provider record and market update must be no later than the requested
as-of time, which is itself the prediction cutoff. Malformed updates and genuine
future values are rejected before persistence.

## Offline evidence

Raw coverage is counted directly from every bookmaker market outcome in the
cached event-object response (not inferred from the persisted artifact). The
offline audit reports provider market counts per event and in aggregate,
normalized coverage, canonical and fallback player identities, collisions, and
coverage-sum invariants. Passing markets are never synthesized: their readiness
is `READY`, `PARTIAL`, or `NOT_READY` according to persisted evidence.
