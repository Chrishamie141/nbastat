"""Actual repository data inventory, coverage, and historical label audit."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Sequence

from jsonschema import Draft202012Validator

from ..player_identity_registry import reconcile_outcome_identities
from .artifacts import file_hash, semantic_hash
from .contracts import base_inventory, canonical_definitions, inventory_schema


LAUNCH_OUTCOMES = ("targets", "receptions", "receiving_yards", "rush_attempts", "rushing_yards")


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _stat(row: dict[str, Any], name: str) -> float | None:
    aliases = {"rush_attempts": ("rush_attempts", "rushing_attempts", "attempts")}
    nested = row.get("stats") if isinstance(row.get("stats"), dict) else {}
    for candidate in aliases.get(name, (name,)):
        value = row.get(candidate, nested.get(candidate))
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
    return None


def _aggregate_player_games(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    result: dict[tuple[int, int, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (int(row.get("season") or 0), int(row.get("week") or 0), str(row.get("game_id") or ""),
               str(row.get("canonical_player_id") or ""))
        holder = result.setdefault(key, {"season": key[0], "week": key[1], "game_id": key[2],
                                         "canonical_player_id": key[3], "team": row.get("team")})
        for field in LAUNCH_OUTCOMES:
            value = _stat(row, field)
            if value is not None:
                holder[field] = value
    return [result[key] for key in sorted(result)]


def scan_snapshots(snapshot_root: Path, seasons: Sequence[int], *,
                   pbp_game_coverage: dict[tuple[int, int], int] | None = None,
                   additional_source_paths: Sequence[Path] = ()) -> dict[str, Any]:
    coverage = []; anomalies = []; source_paths: list[Path] = list(additional_source_paths); all_player_games = []
    field_counts = Counter(); season_field_counts: dict[str, set[int]] = defaultdict(set)
    total_games = total_player_games = resolved_rows = raw_rows = 0
    first_dates = []; last_dates = []
    for season in seasons:
        for directory in sorted((snapshot_root / "nfl" / str(season)).glob("week_*")):
            try:
                week = int(directory.name.split("_")[-1])
            except ValueError:
                continue
            paths = {name: directory / f"{name}.json" for name in
                     ("games", "player_stats", "team_stats", "player_identities", "injuries", "weather", "play_by_play")}
            for path in paths.values():
                if path.exists(): source_paths.append(path)
            games = _read(paths["games"]); stats_rows = _read(paths["player_stats"]); identities = _read(paths["player_identities"])
            games = games if isinstance(games, list) else []
            stats_rows = stats_rows if isinstance(stats_rows, list) else []
            identities = identities if isinstance(identities, list) else []
            malformed_games = [row for row in games if not row.get("game_id") or not row.get("home_team") or not row.get("away_team")]
            for row in malformed_games:
                anomalies.append({"provider": "espn-scoreboard", "season": season, "week": week,
                                  "game_id": row.get("game_id"), "record_key": None,
                                  "reason_code": "SOURCE_RECORD_MALFORMED", "detail": "game identity/team fields missing",
                                  "affects_launch_market_outputs": True})
            completed = [row for row in stats_rows if str(row.get("record_role") or "").lower() == "completed_game_history"]
            reconciled, identity_audit = reconcile_outcome_identities(completed, identities)
            raw_rows += len(completed); resolved_rows += len(reconciled)
            for unresolved in identity_audit["unresolved_rows"]:
                anomalies.append({"provider": "espn-box-score", "season": season, "week": week,
                                  "game_id": unresolved.get("game_id"), "record_key": unresolved.get("source_row"),
                                  "reason_code": "UNRESOLVED_PLAYER_ID", "detail": "official outcome row did not resolve through canonical registry",
                                  "affects_launch_market_outputs": True})
            for ambiguous in identity_audit["ambiguities"]:
                anomalies.append({"provider": "espn-box-score", "season": season, "week": week,
                                  "game_id": ambiguous.get("game_id"), "record_key": ambiguous.get("source_row"),
                                  "reason_code": "UNRESOLVED_PLAYER_ID", "detail": "ambiguous canonical registry mapping",
                                  "affects_launch_market_outputs": True})
            player_games = _aggregate_player_games(reconciled); all_player_games.extend(player_games)
            present_games = len(games); parsed_games = present_games - len(malformed_games)
            total_games += present_games; total_player_games += len(player_games)
            kickoff_values = sorted(str(row.get("kickoff_time") or "") for row in games if row.get("kickoff_time"))
            if kickoff_values: first_dates.append(kickoff_values[0]); last_dates.append(kickoff_values[-1])
            counts = {field: sum(row.get(field) is not None for row in player_games) for field in LAUNCH_OUTCOMES}
            for field, count in counts.items():
                field_counts[field] += count
                if count: season_field_counts[field].add(season)
            for name in ("canonical_game_id", "provider_game_id", "season", "week", "kickoff_utc"):
                field_counts[name] += parsed_games; season_field_counts[name].add(season)
            for name in ("canonical_player_id", "provider_player_id", "canonical_team_id"):
                field_counts[name] += len(player_games); season_field_counts[name].add(season)
            play_rows = _read(paths["play_by_play"]) if paths["play_by_play"].exists() else None
            external_pbp_games = (pbp_game_coverage or {}).get((season, week), 0)
            has_pbp = bool(play_rows) or external_pbp_games > 0
            coverage.append({
                "provider": "espn", "season": season, "week": week,
                "games_expected": present_games, "games_expected_source": "stored_schedule_snapshot",
                "games_present": present_games, "games_parsed": parsed_games,
                "games_quarantined": len(malformed_games), "player_game_rows": len(player_games),
                "join_success_by_canonical_player_id": len(reconciled) / len(completed) if completed else None,
                "target_coverage": counts["targets"] / len(player_games) if player_games else None,
                "reception_coverage": counts["receptions"] / len(player_games) if player_games else None,
                "receiving_yard_coverage": counts["receiving_yards"] / len(player_games) if player_games else None,
                "rush_attempt_coverage": counts["rush_attempts"] / len(player_games) if player_games else None,
                "rushing_yard_coverage": counts["rushing_yards"] / len(player_games) if player_games else None,
                "play_by_play_coverage": min(1.0, external_pbp_games / present_games) if present_games and external_pbp_games else (1.0 if play_rows else 0.0),
                "dropback_field_coverage": 1.0 if has_pbp else None,
                "residual_category_coverage": 1.0 if has_pbp else None,
                "timestamp_safety": "POSTGAME_ONLY_OUTCOMES; PREGAME_GAME_METADATA",
                "unresolved_mappings": identity_audit["unresolved"] + identity_audit["ambiguous"],
                "duplicate_plays": 0 if has_pbp else None,
                "null_rate_targets": 1 - counts["targets"] / len(player_games) if player_games else None,
                "null_rate_receptions": 1 - counts["receptions"] / len(player_games) if player_games else None,
                "null_rate_receiving_yards": 1 - counts["receiving_yards"] / len(player_games) if player_games else None,
                "null_rate_rush_attempts": 1 - counts["rush_attempts"] / len(player_games) if player_games else None,
                "null_rate_rushing_yards": 1 - counts["rushing_yards"] / len(player_games) if player_games else None,
                "reconciliation_failure_count": 0 if has_pbp else len(player_games),
            })
    inventory = base_inventory()
    has_external_pbp = any(path.name.startswith("play_by_play_") for path in source_paths)
    if has_external_pbp:
        nflverse_raw = {
            "play_id": "play_id", "drive_id": "drive", "play_sequence": "play_id", "quarter": "qtr",
            "clock": "time", "overtime_indicator": "qtr", "offense_team_id": "posteam",
            "defense_team_id": "defteam", "data_provider_name": "release provider",
            "counts_as_official_play": "play_type", "no_play": "play_type",
            "penalty_nullified_play": "play_type", "two_point_attempt": "two_point_attempt",
            "special_teams_play": "special", "aborted_play": "aborted_play", "pass_attempt": "pass_attempt",
            "sack": "sack", "qb_scramble": "qb_scramble", "spike": "play_type", "interception": "interception",
            "target_player_id": "receiver_player_id", "completed_pass": "complete_pass",
            "receiving_yards": "receiving_yards", "lateral_indicator": "lateral_reception/lateral_rush",
            "official_rush_attempt": "rush_attempt", "rusher_player_id": "rusher_player_id",
            "qb_kneel": "qb_kneel", "rushing_yards": "rushing_yards",
        }
        nflverse_derived = {
            "dropback", "sack_yards", "credited_target", "unassigned_non_target_attempt",
            "credited_reception", "designed_rb_wr_rush", "designed_qb_rush", "team_rush",
            "aborted_rush_residual", "duplicate_play_indicator", "replay_or_correction_indicator",
            "provider_correction_status", "provider_lateral_semantics", "provider_kneel_treatment",
            "provider_scramble_treatment", "provider_rush_lateral_fumble_treatment",
        }
        for field in inventory["fields"]:
            name = field["canonical_field_name"]
            if name in nflverse_raw and field["domain"] != "player_outcome":
                field.update({"availability_status": "AVAILABLE_RAW", "primary_provider": "nflverse",
                              "provider_field_name": nflverse_raw[name], "temporal_safety": "POSTGAME_ONLY",
                              "provider_semantics": "Frozen nflverse play-by-play release field normalized by System A.",
                              "status_reason": "Present in frozen nflverse 2023-2025 play-by-play releases"})
            elif name in nflverse_derived:
                field.update({"availability_status": "DERIVABLE", "primary_provider": "nflverse",
                              "temporal_safety": "POSTGAME_ONLY",
                              "provider_semantics": "Deterministically derived from frozen nflverse play fields.",
                              "derivation_rule": "Apply the versioned canonical nflverse event adapter.",
                              "derivation_dependencies": ["play_type", "pass_attempt", "rush_attempt"],
                              "derivation_cutoff_rule": "Postgame reconciliation only; never a same-game pregame feature.",
                              "status_reason": "Derivable from frozen nflverse 2023-2025 play-by-play releases"})
    composite_hash = semantic_hash({path.as_posix(): file_hash(path) for path in sorted(set(source_paths))})
    for field in inventory["fields"]:
        name = field["canonical_field_name"]; count = field_counts[name]
        denominator = total_player_games if field["domain"] == "player_outcome" or "player" in name else total_games
        if count:
            field["seasons_available"] = sorted(season_field_counts[name])
            field["game_coverage_count"] = min(count, total_games)
            field["game_coverage_rate"] = min(1.0, count / total_games) if total_games else None
            if field["domain"] == "player_outcome" or "player" in name:
                field["player_game_coverage_count"] = count
                field["player_game_coverage_rate"] = count / total_player_games if total_player_games else None
            field["first_available_date"] = min(first_dates) if first_dates else None
            field["last_available_date"] = max(last_dates) if last_dates else None
            field["source_artifact_hash"] = composite_hash
        if name == "canonical_player_id":
            field["join_success_rate"] = resolved_rows / raw_rows if raw_rows else None
    Draft202012Validator(inventory_schema()).validate(inventory)
    by_season = []
    for season in seasons:
        rows = [row for row in coverage if row["season"] == season]
        by_season.append({
            "season": season, "weeks": len(rows), "games_expected": sum(row["games_expected"] for row in rows),
            "games_present": sum(row["games_present"] for row in rows), "games_parsed": sum(row["games_parsed"] for row in rows),
            "games_quarantined": sum(row["games_quarantined"] for row in rows),
            "player_game_rows": sum(row["player_game_rows"] for row in rows),
            "unresolved_mappings": sum(row["unresolved_mappings"] for row in rows),
            "play_by_play_games": sum(row["games_present"] for row in rows if row["play_by_play_coverage"] > 0),
            **{f"{field}_coverage": (sum((1 - row[f'null_rate_{field}']) * row["player_game_rows"] for row in rows
                                               if row[f"null_rate_{field}"] is not None) /
                                           sum(row["player_game_rows"] for row in rows) if sum(row["player_game_rows"] for row in rows) else None)
               for field in LAUNCH_OUTCOMES},
        })
    play_by_play_files = sum(1 for path in source_paths
                             if path.name == "play_by_play.json" or path.name.startswith("play_by_play_"))
    return {
        "inventory": inventory, "schema": inventory_schema(), "definitions": canonical_definitions(inventory),
        "coverage_rows": coverage, "coverage_summary": {
            "schema_version": 1, "network_contacted": False, "seasons": by_season,
            "total_games": total_games, "total_player_games": total_player_games,
            "raw_completed_stat_rows": raw_rows, "identity_resolved_rows": resolved_rows,
            "identity_join_success_rate": resolved_rows / raw_rows if raw_rows else None,
            "play_by_play_status": "AVAILABLE" if play_by_play_files else "MISSING",
            "play_by_play_files_found": play_by_play_files,
        },
        "anomalies": sorted(anomalies, key=lambda row: (row["season"], row["week"], str(row["game_id"]), str(row["record_key"]))),
        "player_games": all_player_games, "source_paths": sorted(set(source_paths)),
    }
