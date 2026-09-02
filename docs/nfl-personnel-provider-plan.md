# NFL personnel input audit

The current configured providers do not supply a reliable, game-specific,
structured preseason personnel feed. The ESPN injuries adapter returns an
empty collection, and the local roster/depth-chart data does not establish
starter participation or expected snaps for a particular game.

| Input | Current status | Future integration requirement |
|---|---|---|
| QB rotations | Unavailable | Licensed or official game-specific rotation feed with source timestamp |
| Starter participation | Unavailable | Team/league status feed normalized by player, game, and publication time |
| Expected snap counts | Unavailable | Structured projection provider with revision history |
| Injuries | Unavailable | Verified injury report with practice/game status and effective timestamp |
| Inactive/rest decisions | Unavailable | Official inactive list or team transaction/status feed |
| Depth-chart changes | Partial | Current roster identities exist; add dated, source-backed depth-chart events |
| Roster transactions | Partial | Normalize official transaction events instead of inferring changes from snapshots |

No speculative scraping should populate these fields. A future provider adapter
must preserve raw provenance, normalize publication/effective timestamps, and
remain isolated from the frozen 2026 preseason Week 3 baseline until evaluated
prospectively.
