"""Canonical opportunity ledgers and accounting invariant gates."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from .events import CanonicalEvent, quarantine


def _sum(rows: Iterable[CanonicalEvent], field: str) -> float:
    return sum(float(getattr(row, field)) for row in rows)


def _event_finding(event: CanonicalEvent) -> dict[str, Any] | None:
    raw = event.as_dict()
    effects = (
        event.increments_dropback, event.increments_pass_attempt, event.increments_sack,
        event.increments_target, event.increments_completion, event.increments_reception,
        event.increments_official_rush_attempt, event.non_target_attempt_count,
        event.team_rush_residual_count,
    )
    if any(value < 0 for value in effects):
        return quarantine(raw, "SOURCE_RECORD_MALFORMED", "negative official-stat increment is unsupported")
    if (event.no_play or event.nullified_by_penalty) and (any(effects) or any((event.receiving_yards, event.rushing_yards, event.sack_yards))):
        return quarantine(raw, "NULLIFIED_PLAY_CONFLICT", "nullified/no-play event carries official-stat effects")
    if event.dropback_result == "SCRAMBLE" and not (
        event.offensive_event_family == "DROPBACK" and event.increments_dropback == 1
        and event.increments_official_rush_attempt == 1 and event.rush_category == "QB_SCRAMBLE"
    ):
        return quarantine(raw, "RUSH_PARTITION_MISMATCH", "scramble is not one cross-ledger canonical event")
    if event.increments_target and not event.target_player_id:
        return quarantine(raw, "UNRESOLVED_PLAYER_ID", "credited target has no canonical player ID")
    if event.increments_reception and not event.receiver_id:
        return quarantine(raw, "UNRESOLVED_PLAYER_ID", "credited reception has no canonical receiver ID")
    if event.increments_reception and event.receiver_id != event.target_player_id:
        return quarantine(raw, "RECEPTIONS_EXCEED_TARGETS",
                          "credited receiver and target identities differ on a completed pass")
    if event.increments_official_rush_attempt and event.team_rush_residual_count == 0 and not event.rusher_id:
        return quarantine(raw, "UNRESOLVED_PLAYER_ID", "player rushing attempt has no canonical rusher ID")
    if event.increments_reception > event.increments_target:
        return quarantine(raw, "RECEPTIONS_EXCEED_TARGETS", "event reception exceeds credited target")
    rush_flags = sum(event.rush_category == value for value in (
        "DESIGNED_RB_WR_RUSH", "DESIGNED_QB_RUSH", "QB_SCRAMBLE", "QB_KNEEL", "TEAM_OR_ABORTED_RESIDUAL"
    ))
    if event.increments_official_rush_attempt and rush_flags != 1:
        return quarantine(raw, "RUSH_PARTITION_MISMATCH", "official rush has zero or multiple canonical categories")
    return None


def build_ledgers(events: Iterable[CanonicalEvent]) -> dict[str, Any]:
    ordered = sorted(events, key=lambda event: (event.canonical_game_id, event.play_sequence, event.play_id))
    quarantine_rows: list[dict[str, Any]] = []
    locally_valid = []
    for event in ordered:
        finding = _event_finding(event)
        if finding:
            quarantine_rows.append(finding)
        else:
            locally_valid.append(event)

    by_team_game: dict[tuple[str, str], list[CanonicalEvent]] = defaultdict(list)
    for event in locally_valid:
        by_team_game[(event.canonical_game_id, event.canonical_offense_team_id)].append(event)
    invalid_games: set[tuple[str, str]] = set()
    for key, rows in sorted(by_team_game.items()):
        dropbacks = int(_sum(rows, "increments_dropback"))
        attempts = int(_sum(rows, "increments_pass_attempt"))
        sacks = int(_sum(rows, "increments_sack"))
        scrambles = sum(row.dropback_result == "SCRAMBLE" for row in rows)
        if dropbacks != attempts + sacks + scrambles:
            invalid_games.add(key)
            quarantine_rows.append(quarantine(
                {"canonical_game_id": key[0], "canonical_offense_team_id": key[1],
                 "dropbacks": dropbacks, "pass_attempts": attempts, "sacks": sacks, "scrambles": scrambles},
                "PASS_ATTEMPT_ALLOCATION_MISMATCH", "dropback partition does not conserve",
            ))
        credited = int(_sum(rows, "increments_target")); residual = int(_sum(rows, "non_target_attempt_count"))
        if attempts != credited + residual:
            invalid_games.add(key)
            quarantine_rows.append(quarantine(
                {"canonical_game_id": key[0], "canonical_offense_team_id": key[1],
                 "pass_attempts": attempts, "credited_targets": credited, "unassigned_attempts": residual},
                "PASS_ATTEMPT_ALLOCATION_MISMATCH", "pass-attempt allocation does not conserve",
            ))
        rushes = int(_sum(rows, "increments_official_rush_attempt"))
        categories = sum(row.rush_category is not None and row.increments_official_rush_attempt for row in rows)
        if rushes != categories:
            invalid_games.add(key)
            quarantine_rows.append(quarantine(
                {"canonical_game_id": key[0], "canonical_offense_team_id": key[1],
                 "official_rush_attempts": rushes, "partitioned_rush_attempts": categories},
                "RUSH_PARTITION_MISMATCH", "rush partition does not conserve",
            ))
    accepted = [event for event in locally_valid
                if (event.canonical_game_id, event.canonical_offense_team_id) not in invalid_games]

    by_team_game = defaultdict(list)
    for event in accepted:
        by_team_game[(event.canonical_game_id, event.canonical_offense_team_id)].append(event)
    dropback_ledger = []; pass_ledger = []; rush_ledger = []
    for (game, team), rows in sorted(by_team_game.items()):
        dropbacks = int(_sum(rows, "increments_dropback")); attempts = int(_sum(rows, "increments_pass_attempt"))
        sacks = int(_sum(rows, "increments_sack")); scrambles = sum(row.dropback_result == "SCRAMBLE" for row in rows)
        dropback_ledger.append({"canonical_game_id": game, "canonical_offense_team_id": team,
                                "dropbacks": dropbacks, "pass_attempts": attempts, "sacks": sacks,
                                "quarterback_scrambles": scrambles,
                                "partition_valid": dropbacks == attempts + sacks + scrambles})
        targets = int(_sum(rows, "increments_target")); residual = int(_sum(rows, "non_target_attempt_count"))
        pass_ledger.append({"canonical_game_id": game, "canonical_offense_team_id": team,
                            "pass_attempts": attempts, "credited_player_targets": targets,
                            "unassigned_non_target_attempts": residual,
                            "allocation_valid": attempts == targets + residual})
        counts = {category: sum(row.rush_category == category and row.increments_official_rush_attempt for row in rows)
                  for category in ("DESIGNED_RB_WR_RUSH", "DESIGNED_QB_RUSH", "QB_SCRAMBLE", "QB_KNEEL",
                                   "TEAM_OR_ABORTED_RESIDUAL")}
        total = int(_sum(rows, "increments_official_rush_attempt"))
        rush_ledger.append({"canonical_game_id": game, "canonical_offense_team_id": team,
                            "total_official_team_rush_attempts": total,
                            "designed_rb_wr_rushes": counts["DESIGNED_RB_WR_RUSH"],
                            "designed_qb_rushes": counts["DESIGNED_QB_RUSH"],
                            "qb_scrambles": counts["QB_SCRAMBLE"], "qb_kneels": counts["QB_KNEEL"],
                            "team_or_aborted_residuals": counts["TEAM_OR_ABORTED_RESIDUAL"],
                            "partition_valid": total == sum(counts.values())})

    player_receiving: dict[tuple[str, str], dict[str, Any]] = {}
    player_rushing: dict[tuple[str, str], dict[str, Any]] = {}
    for event in accepted:
        if event.increments_target and event.target_player_id:
            key = (event.canonical_game_id, event.target_player_id)
            row = player_receiving.setdefault(key, {"canonical_game_id": key[0], "canonical_player_id": key[1],
                                                     "targets": 0, "receptions": 0, "receiving_yards": 0.0})
            row["targets"] += event.increments_target
        if event.increments_reception and event.receiver_id:
            key = (event.canonical_game_id, event.receiver_id)
            row = player_receiving.setdefault(key, {"canonical_game_id": key[0], "canonical_player_id": key[1],
                                                     "targets": 0, "receptions": 0, "receiving_yards": 0.0})
            row["receptions"] += event.increments_reception; row["receiving_yards"] += event.receiving_yards
        if event.increments_official_rush_attempt and event.rusher_id:
            key = (event.canonical_game_id, event.rusher_id)
            row = player_rushing.setdefault(key, {"canonical_game_id": key[0], "canonical_player_id": key[1],
                                                  "rush_attempts": 0, "rushing_yards": 0.0})
            row["rush_attempts"] += event.increments_official_rush_attempt; row["rushing_yards"] += event.rushing_yards
    for _key, row in sorted(player_receiving.items()):
        if row["receptions"] > row["targets"]:
            quarantine_rows.append(quarantine(row, "RECEPTIONS_EXCEED_TARGETS", "player receptions exceed credited targets"))
    receiving_rows = [row for key, row in sorted(player_receiving.items()) if row["receptions"] <= row["targets"]]
    rushing_rows = [row for _key, row in sorted(player_rushing.items())]
    return {
        "accepted_events": [event.as_dict() for event in accepted],
        "team_game_dropback_ledger": dropback_ledger,
        "team_game_pass_attempt_allocation_ledger": pass_ledger,
        "player_game_target_reception_ledger": receiving_rows,
        "team_game_rush_partition_ledger": rush_ledger,
        "player_game_rushing_ledger": rushing_rows,
        "quarantine": sorted(quarantine_rows, key=lambda row: (
            str(row.get("season") or ""), str(row.get("week") or ""), str(row.get("game_id") or ""),
            str(row.get("play_id") or ""), str(row.get("reason_code") or ""),
        )),
        "reconciliation": {
            "input_events": len(ordered), "accepted_events": len(accepted),
            "quarantined_findings": len(quarantine_rows),
            "accepted_rows_unresolved_accounting_violations": 0,
            "dropback_partitions_valid": all(row["partition_valid"] for row in dropback_ledger),
            "pass_allocations_valid": all(row["allocation_valid"] for row in pass_ledger),
            "rush_partitions_valid": all(row["partition_valid"] for row in rush_ledger),
        },
    }
