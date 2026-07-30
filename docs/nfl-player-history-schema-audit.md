# NFL player-history schema audit

## Source snapshot contract

The persisted ESPN rows are **category-split wide rows**: one row represents one
player/game/statistical category (`passing`, `rushing`, or `receiving`) and its
`stats` object contains several values. They are not canonical player-game rows.
The source keys are `game_id`, `player` (legacy display-name identity), `team`,
`season`, `through_week`, `week`, `stats`, `source`, `captured_at`, `data_as_of`,
`is_pregame`, and `record_role`; newly built rows also retain `player_id`,
`player_name`, and `position` from ESPN. ESPN statistics map as follows:

| ESPN representation | Canonical value |
|---|---|
| passing `C/ATT` | `completions`, `passing_attempts` |
| passing `YDS`, `TD` | `passing_yards`, `passing_tds` |
| rushing `CAR`, `YDS`, `TD` | `rushing_attempts`, `rushing_yards`, `rushing_tds` |
| receiving `TGTS`, `REC`, `YDS`, `TD` | `targets`, `receptions`, `receiving_yards`, `receiving_tds` |

Values are numeric inside `stats`; genuine zeroes are retained. Provenance is
ESPN. The source does not provide a box-score publication timestamp.

## Root cause of zero eligible rows

The snapshot builder read the **target game's postgame box score**, labeled it
`pregame_history`, and assigned kickoff to `captured_at` and `data_as_of`.
Furthermore, statistics remained nested under `stats`, whereas the simulator
looked for top-level canonical names. The explicit prediction cutoff is normally
before kickoff, so strict filtering rejected every such target-week source row as
future. Even absent that filter, the schema mismatch would have produced zero
usage values. Repeating the same weekly file for every target game explains why
7,369 discovered rows became 114,481 loaded rows; it does not represent 114,481
unique observations.

The derived normalization layer now assigns completed-box-score observations a
conservative `known_at` of kickoff plus six hours, combines category rows by
provider player ID/game/team, and reads prior weekly snapshots once through a
cached cross-week index. It never changes the source files.

## Canonical contract and readiness

Canonical rows contain league/season/week/game, provider-first player identity,
name/team/opponent/normalized position, completion/provenance timestamps,
completed-game record role, source, and all supported nullable stat fields.
Equal duplicates collapse; conflicting values are reported and omitted. Name
fallbacks are normalized and team-scoped, with collisions reported.

Feature history and postgame outcome grading are independently reported for
passing yards/TDs, rushing attempts/yards, receptions, and receiving yards.
Existing historical odds snapshots use the game-market endpoint (h2h, spread,
total), so historical player pricing, EV, ROI, and SGP price comparison remain
**not ready**. Player simulation is structurally ready where eligible history
exists; ticket selection is intentionally out of scope and historical player
prices remain the largest SGP blocker.
