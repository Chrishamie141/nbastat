# Historical NFL roster identity acquisition

ESPN was selected because it is the repository's existing canonical NFL
schedule, summary, and statistics provider. Its team-roster API supplies names,
athlete IDs, and positions without an arbitrary scraper or paid dependency. The
adapter is isolated in `backtesting/historical_roster_acquisition.py`; raw
responses use the shared `JsonRawCache` convention.

## Historical safety and limitations

A `season=YYYY` query is not proof that ESPN served a point-in-time roster.
Eligibility therefore depends on raw-cache metadata: capture must be no later
than the historical prediction cutoff, and request identity must match team and
season while normalized evidence must match the replay week. Capture time is
copied to `captured_at`, `known_at`, and `data_as_of`: it means this exact
response was observable by then, not that every membership began then.

Evidence without that proof—including today's response requested for an old
season—is rejected. Thus the production 2025 Week 1 gap cannot be repaired from
the network today unless a contemporaneous immutable response is supplied by an
external archive. The system reports that limitation rather than fabricating
historical identities.

Matching remains exact and game/team scoped. Provider athlete IDs take
precedence; otherwise the existing deterministic name/team/game fallback is
used. Same-name collisions remain `AMBIGUOUS`; absent evidence remains
`UNKNOWN`. No fuzzy aliases are introduced.

## Operation

```bash
# Read-only plan; zero network calls:
python -m backtesting.historical_roster_acquisition \
  --season 2025 --week 1 --plan

# Explicit free-network opt-in (appropriate only before the cutoff):
python -m backtesting.historical_roster_acquisition \
  --season 2025 --week 1 --allow-network
```

The resumable, cache-first plan lists game/team requests, cache hits, missing
coverage, network need, and a zero paid-quota estimate. Reports enumerate
identities, provider-ID coverage, covered team/weeks, and rejected scope.
Registry/rebuild diagnostics also report identities added beyond existing game
evidence, roster-only reconciliations, and unresolved players by
game/team/market.

Roster evidence proves identity and scoped membership only. It does not prove a
player dressed or appeared and never supplies statistical participation or
outcomes. Grading continues to require independent `player_stats.json` evidence.
