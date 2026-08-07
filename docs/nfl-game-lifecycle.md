# NFL game lifecycle and data authority

The public game-detail contract is `GET /api/nfl/games/{canonical_game_id}`. The dashboard and detail page must consume its canonical lifecycle vocabulary instead of interpreting provider strings independently.

## Source priority

| Concept | Authority | Notes |
| --- | --- | --- |
| Schedule and canonical game identity | Existing free ESPN scoreboard feed | ESPN numeric event ID is the canonical route/cache key. |
| Live/final status and final score | Existing free ESPN scoreboard/summary feed | Provider values pass through `normalize_game_status`; a score plus official box score can deterministically repair a stale non-final schedule state. |
| Team and player box score | Existing free ESPN summary feed | Presented as actual output only; it never enters pregame context. |
| Frozen predictions | Versioned `backtesting/data/snapshots/nfl/{season}/week_{week}/player_prop_predictions.json` System A artifact | Read-only presentation adapter. No model generation or sportsbook data is part of game refresh. |

Preseason, regular season, and postseason week keys include season phase (for example, `2026:preseason:w1`) so equal week numbers cannot collide.

## Refresh and caching

- Live/halftime: 15-second upstream cache TTL.
- Pregame: 60 seconds; scheduled within 24 hours: 300 seconds; scheduled within one hour: 60 seconds; scheduled more than 24 hours away: 30 minutes.
- Recently final with box-score data: 300 seconds until it is at least 24 hours old.
- Final without box-score data: 60 seconds.
- Stable old final: 24 hours.

Dashboard and detail manual refresh endpoints are authenticated, debounced, and lock-coalesced. Browsers call the application API only; ESPN calls are performed server-side. Failed refreshes leave the last known good cache entry intact.

## Operator audit

Run a live audit with:

```powershell
py -3.14 tools/audit_nfl_game_states.py --game-id <ESPN_GAME_ID>
```

For deterministic fixture validation:

```powershell
py -3.14 tools/audit_nfl_game_states.py --game-id 401000001 --fixture tests/fixtures/nfl_game_detail_completed_preseason.json --output reports/nfl_game_state_audit.fixture.json
```

The report records source/local timestamps, teams, score/stat availability, reason codes, repair action, and result. Automatic repair is limited to unambiguous provider-final transitions or a past game with both final scores and official box-score statistics; other stale states remain reported for review.
