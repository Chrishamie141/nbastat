# NFL System A: Milestones 0 and 1

System A is the sportsbook-independent NFL data foundation for receiving yards, receptions, and rushing yards. It consumes only frozen football source artifacts. It does not import odds, prices, implied probabilities, EV logic, ticket construction, or model code.

The play-by-play source is the nflverse `pbp` release for 2023–2025, with the nflverse `players` release providing the GSIS-to-ESPN identifier crosswalk. These public datasets are distributed under CC-BY 4.0; attribution: nflverse and its contributors. Source URLs and SHA-256 hashes are retained in the provider-ingestion audit and artifact manifest. Downloads are immutable workflow inputs—the build itself performs no network requests.

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

Milestone 0 passes only when its formal inventory validates and required contract fields exist. Milestone 1 historical acceptance additionally requires frozen play-by-play and successful reconciliation to official outcomes. Provider differences, unresolved IDs, and unresolved lateral allocation exclude the affected game from accepted historical ledgers and remain visible in `quarantine.csv`. The workflow never synthesizes events from box scores.
