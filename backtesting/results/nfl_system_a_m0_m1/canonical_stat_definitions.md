# Canonical NFL Stat Definitions v1

Provider behavior is normalized explicitly; these definitions do not claim universal provider semantics.

## `aborted_play`

Frozen nflverse play-by-play release field normalized by System A.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `aborted_rush_residual`

Deterministically derived from frozen nflverse play fields.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `active_inactive_status`

Unavailable in stored historical snapshots or lacks verified historical publication timing.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Context metadata is not an official player-stat total.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `batted_or_tipped_unassigned_attempt`

Canonical play-event field; current repository has no stored play-by-play provider export.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `canonical_game_id`

ESPN scoreboard/game or box-score identifier retained in frozen snapshots.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Exact canonical registry join; ambiguous mappings are quarantined.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `canonical_player_id`

ESPN scoreboard/game or box-score identifier retained in frozen snapshots.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Exact canonical registry join; ambiguous mappings are quarantined.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `canonical_team_id`

ESPN scoreboard/game or box-score identifier retained in frozen snapshots.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Exact canonical registry join; ambiguous mappings are quarantined.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `clock`

Frozen nflverse play-by-play release field normalized by System A.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Exact canonical registry join; ambiguous mappings are quarantined.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `completed_pass`

Frozen nflverse play-by-play release field normalized by System A.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `counts_as_official_play`

Frozen nflverse play-by-play release field normalized by System A.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `credited_reception`

Deterministically derived from frozen nflverse play fields.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `credited_target`

Deterministically derived from frozen nflverse play fields.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `data_provider_name`

Frozen nflverse play-by-play release field normalized by System A.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Exact canonical registry join; ambiguous mappings are quarantined.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `data_provider_version`

Requires play-by-play source not stored.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Exact canonical registry join; ambiguous mappings are quarantined.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `defense_team_id`

Frozen nflverse play-by-play release field normalized by System A.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Exact canonical registry join; ambiguous mappings are quarantined.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `defensive_play`

Canonical play-event field; current repository has no stored play-by-play provider export.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `depth_chart_status`

Unavailable in stored historical snapshots or lacks verified historical publication timing.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Context metadata is not an official player-stat total.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `designed_qb_rush`

Deterministically derived from frozen nflverse play fields.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `designed_rb_wr_rush`

Deterministically derived from frozen nflverse play fields.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `drive_id`

Frozen nflverse play-by-play release field normalized by System A.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Exact canonical registry join; ambiguous mappings are quarantined.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `dropback`

Deterministically derived from frozen nflverse play fields.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `duplicate_play_indicator`

Deterministically derived from frozen nflverse play fields.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `field_surface`

Unavailable in stored historical snapshots or lacks verified historical publication timing.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Context metadata is not an official player-stat total.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `inactive_status`

Unavailable in stored historical snapshots or lacks verified historical publication timing.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Context metadata is not an official player-stat total.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `injury_report_timestamp`

Unavailable in stored historical snapshots or lacks verified historical publication timing.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Context metadata is not an official player-stat total.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `injury_status`

Unavailable in stored historical snapshots or lacks verified historical publication timing.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Context metadata is not an official player-stat total.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `interception`

Frozen nflverse play-by-play release field normalized by System A.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `kickoff_utc`

ESPN scoreboard/game or box-score identifier retained in frozen snapshots.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Exact canonical registry join; ambiguous mappings are quarantined.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `kickoff_weather`

Unavailable in stored historical snapshots or lacks verified historical publication timing.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Context metadata is not an official player-stat total.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `lateral_indicator`

Frozen nflverse play-by-play release field normalized by System A.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `no_play`

Frozen nflverse play-by-play release field normalized by System A.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `offense_team_id`

Frozen nflverse play-by-play release field normalized by System A.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Exact canonical registry join; ambiguous mappings are quarantined.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `official_rush_attempt`

Frozen nflverse play-by-play release field normalized by System A.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `overtime_indicator`

Frozen nflverse play-by-play release field normalized by System A.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Exact canonical registry join; ambiguous mappings are quarantined.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `pass_attempt`

Frozen nflverse play-by-play release field normalized by System A.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `penalty_nullified_play`

Frozen nflverse play-by-play release field normalized by System A.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `play_id`

Frozen nflverse play-by-play release field normalized by System A.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Exact canonical registry join; ambiguous mappings are quarantined.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `play_sequence`

Frozen nflverse play-by-play release field normalized by System A.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Exact canonical registry join; ambiguous mappings are quarantined.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `precipitation`

Unavailable in stored historical snapshots or lacks verified historical publication timing.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Context metadata is not an official player-stat total.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `provider_correction_status`

Deterministically derived from frozen nflverse play fields.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `provider_game_id`

ESPN scoreboard/game or box-score identifier retained in frozen snapshots.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Exact canonical registry join; ambiguous mappings are quarantined.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `provider_kneel_treatment`

Deterministically derived from frozen nflverse play fields.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `provider_lateral_semantics`

Deterministically derived from frozen nflverse play fields.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `provider_player_id`

ESPN scoreboard/game or box-score identifier retained in frozen snapshots.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Exact canonical registry join; ambiguous mappings are quarantined.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `provider_rush_lateral_fumble_treatment`

Deterministically derived from frozen nflverse play fields.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `provider_scramble_treatment`

Deterministically derived from frozen nflverse play fields.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `qb_kneel`

Frozen nflverse play-by-play release field normalized by System A.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `qb_scramble`

Frozen nflverse play-by-play release field normalized by System A.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `quarter`

Frozen nflverse play-by-play release field normalized by System A.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Exact canonical registry join; ambiguous mappings are quarantined.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `receiving_yards`

Official player box-score outcome for the completed game.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `receptions`

Official player box-score outcome for the completed game.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Must equal the player total aggregated from accepted canonical events.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `replay_or_correction_indicator`

Deterministically derived from frozen nflverse play fields.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `rest_days`

Unavailable in stored historical snapshots or lacks verified historical publication timing.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Context metadata is not an official player-stat total.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `roof_status`

Unavailable in stored historical snapshots or lacks verified historical publication timing.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Context metadata is not an official player-stat total.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `route_share`

Unavailable in stored historical snapshots or lacks verified historical publication timing.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Context metadata is not an official player-stat total.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `rush_attempts`

Official player box-score outcome for the completed game.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Must equal the player total aggregated from accepted canonical events.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `rusher_player_id`

Frozen nflverse play-by-play release field normalized by System A.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `rushing_yards`

Official player box-score outcome for the completed game.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `sack`

Frozen nflverse play-by-play release field normalized by System A.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `sack_yards`

Deterministically derived from frozen nflverse play fields.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `season`

ESPN scoreboard/game or box-score identifier retained in frozen snapshots.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Exact canonical registry join; ambiguous mappings are quarantined.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `snap_share`

Unavailable in stored historical snapshots or lacks verified historical publication timing.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Context metadata is not an official player-stat total.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `special_teams_play`

Frozen nflverse play-by-play release field normalized by System A.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `spike`

Frozen nflverse play-by-play release field normalized by System A.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `starting_qb`

Unavailable in stored historical snapshots or lacks verified historical publication timing.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Context metadata is not an official player-stat total.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `starting_qb_continuity`

Unavailable in stored historical snapshots or lacks verified historical publication timing.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Context metadata is not an official player-stat total.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `target_player_id`

Frozen nflverse play-by-play release field normalized by System A.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `targets`

Official player box-score outcome for the completed game.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Must equal the player total aggregated from accepted canonical events.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `targets_per_team_dropback_proxy`

A target-frequency proxy. It is explicitly not route share.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Context metadata is not an official player-stat total.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `team_rush`

Deterministically derived from frozen nflverse play fields.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `temperature`

Unavailable in stored historical snapshots or lacks verified historical publication timing.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Context metadata is not an official player-stat total.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `throwaway`

Canonical play-event field; current repository has no stored play-by-play provider export.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `travel_time_zone_context`

Unavailable in stored historical snapshots or lacks verified historical publication timing.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Context metadata is not an official player-stat total.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `two_point_attempt`

Frozen nflverse play-by-play release field normalized by System A.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `unassigned_non_target_attempt`

Deterministically derived from frozen nflverse play fields.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Aggregated canonical events must reconcile to official box-score totals.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `week`

ESPN scoreboard/game or box-score identifier retained in frozen snapshots.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Exact canonical registry join; ambiguous mappings are quarantined.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.

## `wind`

Unavailable in stored historical snapshots or lacks verified historical publication timing.

- Inclusion: Include only records within the field domain that pass identity and validity gates.
- Exclusion: Exclude no-play, nullified, duplicate-loser, malformed, unresolved-identity, and out-of-scope records.
- Nulls: null means unknown or unavailable; it must not be interpreted as zero
- Reconciliation: Context metadata is not an official player-stat total.
- Sacks: Dropbacks, not pass attempts; sack yards remain separate from player passing yards.
- Scrambles: One DROPBACK/SCRAMBLE event that also increments official player rushing attempts.
- Kneels: One KNEEL event; canonical policy follows provider-normalized official rushing totals.
- Laterals: Quarantined when provider receiving/rushing allocation cannot be resolved.
