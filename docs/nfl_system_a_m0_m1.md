# NFL System A: Milestones 0 and 1

System A is the sportsbook-independent NFL data foundation for receiving yards, receptions, and rushing yards. It consumes only frozen football source artifacts. It does not import odds, prices, implied probabilities, EV logic, ticket construction, or model code.

## Commands

```powershell
python -m backtesting.system_a.workflow all --snapshot-root backtesting/data/snapshots --output-dir backtesting/results/nfl_system_a_m0_m1
python -m backtesting.system_a.workflow verify --output-dir backtesting/results/nfl_system_a_m0_m1 --compare-dir work/nfl_system_a_m0_m1_determinism
```

`inventory`, `coverage`, `events`, `ledgers`, and `audit` are accepted command aliases for the same atomic full build. This prevents partially refreshed artifacts from being mistaken for a coherent release.

## Information-time policy

Pregame features may depend only on records whose verified information timestamp is strictly earlier than the forecast cutoff. Missing, unverified, equal-to-cutoff, or post-cutoff timestamps fail closed. Postgame player statistics are training targets and reconciliation data, never pregame features.

## Provider semantics and quarantine

Canonical definitions are versioned in generated JSON and Markdown. Nullified plays and two-point attempts have zero launch-market effects. Sacks, scrambles, spikes, throwaways, batted passes, kneels, aborted/team rushes, overtime, laterals, duplicates, and corrections have explicit rules. Unresolved identities, semantics, or accounting contradictions are excluded into `quarantine.csv`; they are never coerced into plausible values.

Milestone 0 passes only when its formal inventory validates and required contract fields exist. Milestone 1 historical acceptance additionally requires frozen play-by-play and successful reconciliation to official outcomes. With no stored play-by-play, the workflow emits empty accepted event ledgers, quarantines unreconciled official player-games, and reports `MISSING_PLAY_BY_PLAY`. It never synthesizes events from box scores.
