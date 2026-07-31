# Historical NFL roster identity acquisition

ESPN was selected because it is the repository's existing canonical NFL
schedule, summary, and statistics provider. Its team-roster API supplies names,
athlete IDs, and positions without an arbitrary scraper or paid dependency. The
adapter is isolated in `backtesting/historical_roster_acquisition.py`; raw
responses use the shared `JsonRawCache` convention.

## Provider verification and historical safety

**Conclusion: ESPN cannot currently provide defensible historical 2025 roster
evidence.** The retained public route is
`/apis/site/v2/sports/football/nfl/teams/{team-abbreviation}/roster?season=YYYY`;
canonical ESPN/NFL abbreviations such as `buf` are accepted. The response is a
JSON team roster, but it does not declare a roster week, roster effective date,
or immutable historical snapshot identity. In particular, the response's
season/request echo does not establish that membership is historical, and no
test establishes that changing the query reconstructs point-in-time membership.
The query must therefore be treated as cosmetic for leakage decisions.

Live verification from this development environment received an intermediary
HTTP 403 (`text/plain`) before reaching ESPN, so production status and encoding
must be inspected with the single-team verifier rather than inferred here. The
verifier reports the sanitized URL, status, content type/encoding, athlete count,
discovered historical scope, and acceptance decision without writing cache:

```bash
python -m backtesting.historical_roster_acquisition \
  --season 2025 --week 1 --verify-team BUF
```

A `season=YYYY` query is not proof that ESPN served a point-in-time roster.
Eligibility therefore depends on raw-cache metadata: a contemporaneous capture must be no later
than the historical prediction cutoff, and request identity must match team and
season. A later download is acceptable only when an external historical provider
persists explicit provider-derived season plus week/effective-date scope that
validates against the replay cutoff. Request time and effective time remain
separate fields. Capture time is
copied to `captured_at`, `known_at`, and `data_as_of`: it means this exact
response was observable by then, not that every membership began then.

Evidence without that proof—including today's response requested for an old
season—is rejected. Thus the production 2025 Week 1 gap cannot be repaired from
the network today unless a contemporaneous immutable response or explicit point-in-time roster is supplied by an
external archive. The missing external capability is a provider that returns
historical NFL membership with an authoritative season and week, roster date,
or effective timestamp. The system reports that limitation rather than fabricating
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
