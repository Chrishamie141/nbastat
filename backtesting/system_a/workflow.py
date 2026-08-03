"""CLI orchestration for NFL System A Milestones 0 and 1."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from .artifacts import file_hash, write_csv, write_json, write_json_gzip, write_text
from .events import CanonicalEvent, normalize_events, quarantine
from .inventory import LAUNCH_OUTCOMES, scan_snapshots
from .ledgers import build_ledgers
from .nflverse import load_nflverse_events


SCHEMA_VERSION = 1
DEFAULT_OUTPUT = Path("backtesting/results/nfl_system_a_m0_m1")
DEFAULT_PBP_ROOT = Path("backtesting/data/system_a/nflverse/pbp/raw")
DEFAULT_PLAYERS = Path("backtesting/data/system_a/nflverse/players/players.csv")
QUARANTINE_FIELDS = ["provider", "season", "week", "game_id", "play_id", "canonical_player_id",
                     "canonical_team_id", "failed_rule", "reason_code", "explanation",
                     "source_artifact_reference", "affects_launch_market_outputs", "disposition", "raw_values"]


def canonical_event_schema() -> dict[str, Any]:
    fields = list(CanonicalEvent.__dataclass_fields__)
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "nfl-system-a-canonical-event-v1",
            "type": "object", "required": fields, "additionalProperties": False,
            "properties": {name: {} for name in fields}, "schema_version": SCHEMA_VERSION}


def _definitions_markdown(definitions: dict[str, Any]) -> str:
    lines = ["# Canonical NFL Stat Definitions v1", "",
             "Provider behavior is normalized explicitly; these definitions do not claim universal provider semantics.", ""]
    for item in definitions["definitions"]:
        lines.extend([
            f"## `{item['canonical_field_name']}`", "", item["football_meaning"], "",
            f"- Inclusion: {item['inclusion_rules']}", f"- Exclusion: {item['exclusion_rules']}",
            f"- Nulls: {item['null_handling']}", f"- Reconciliation: {item['reconciliation_expectation']}",
            f"- Sacks: {item['sacks']}", f"- Scrambles: {item['scrambles']}",
            f"- Kneels: {item['kneels']}", f"- Laterals: {item['laterals']}", "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def _audit_markdown(summary: dict[str, Any]) -> str:
    lines = ["# NFL System A Milestone 0 Audit", "", "This audit is generated only from frozen local repository artifacts.", "",
             "| Season | Weeks | Games | Player-games | Identity unresolved | Play-by-play games |",
             "|---:|---:|---:|---:|---:|---:|"]
    for row in summary["seasons"]:
        lines.append(f"| {row['season']} | {row['weeks']} | {row['games_present']} | {row['player_game_rows']} | "
                     f"{row['unresolved_mappings']} | {row['play_by_play_games']} |")
    join_rate = summary["identity_join_success_rate"]
    join_text = "not measurable" if join_rate is None else f"{join_rate:.6f}"
    lines.extend(["", f"- Player identity join success: {join_text}",
                  f"- Play-by-play status: **{summary['play_by_play_status']}**", "- Injury snapshots: stored but empty/unverified where present.",
                  "- Weather snapshots: stored but empty where present.",
                  "- No sportsbook, price, implied-probability, EV, or ticket data was scanned.", ""])
    return "\n".join(lines)


def _load_play_records(snapshot_root: Path, seasons: Sequence[int]) -> tuple[list[dict[str, Any]], list[Path]]:
    rows = []; paths = []
    for season in seasons:
        for path in sorted((snapshot_root / "nfl" / str(season)).glob("week_*/play_by_play.json")):
            value = json.loads(path.read_text(encoding="utf-8")); paths.append(path)
            if isinstance(value, list): rows.extend(value)
    return rows, paths


def _historical_reconciliation(ledgers: dict[str, Any], player_games: Sequence[dict[str, Any]],
                               events: Sequence[CanonicalEvent]) -> tuple[dict[str, Any], list[dict[str, Any]], list[CanonicalEvent]]:
    if not events:
        findings = [quarantine(
            {"provider_name": "espn-box-score", "season": row["season"], "week": row["week"],
             "canonical_game_id": row["game_id"], "canonical_player_id": row["canonical_player_id"],
             "source_reference": "player_stats.json", **{field: row.get(field) for field in LAUNCH_OUTCOMES}},
            "PROVIDER_SEMANTICS_UNRESOLVED",
            "official player outcome cannot be reconciled because no canonical play-by-play source is stored",
        ) for row in player_games]
        return ({"status": "BLOCKED_MISSING_PLAY_BY_PLAY", "official_player_games": len(player_games),
                 "reconciled_player_games": 0, "unreconciled_player_games": len(player_games),
                 "accepted_rows_unresolved_accounting_violations": 0,
                 "milestone_1_historical_acceptance": False}, findings, [])
    receiving = {(row["canonical_game_id"], row["canonical_player_id"]): row
                 for row in ledgers["player_game_target_reception_ledger"]}
    rushing = {(row["canonical_game_id"], row["canonical_player_id"]): row
               for row in ledgers["player_game_rushing_ledger"]}
    findings = []; bad_games = set()
    for official in player_games:
        key = (official["game_id"], official["canonical_player_id"])
        actual = {**receiving.get(key, {}), **rushing.get(key, {})}
        mismatches = {field: {"official": official.get(field), "canonical": actual.get(field)}
                      for field in ("targets", "receptions", "receiving_yards", "rush_attempts", "rushing_yards")
                      if official.get(field) is not None and actual.get(field) != official.get(field)}
        if mismatches:
            bad_games.add(key[0]); findings.append(quarantine(
                {"provider_name": "espn-box-score", "season": official["season"], "week": official["week"],
                 "canonical_game_id": key[0], "canonical_player_id": key[1], "mismatches": mismatches},
                "PLAYER_TEAM_TOTAL_MISMATCH", "canonical player totals do not match official outcome",
            ))
    accepted_events = [event for event in events if event.canonical_game_id not in bad_games]
    accepted_player_games = sum(official["game_id"] not in bad_games for official in player_games)
    return ({"status": "PASS" if not findings else "PASS_WITH_QUARANTINE", "official_player_games": len(player_games),
             "accepted_reconciled_player_games": accepted_player_games,
             "excluded_player_games": len(player_games) - accepted_player_games,
             "discrepant_player_games": len(findings),
             "excluded_games": len(bad_games), "accepted_rows_unresolved_accounting_violations": 0,
             "milestone_1_historical_acceptance": True}, findings, accepted_events)


def build_workflow(*, snapshot_root: Path, output_dir: Path,
                   seasons: Sequence[int] = (2023, 2024, 2025),
                   pbp_root: Path = DEFAULT_PBP_ROOT, players_path: Path = DEFAULT_PLAYERS) -> dict[str, Any]:
    provider = (load_nflverse_events(raw_root=pbp_root, players_path=players_path,
                                     snapshot_root=snapshot_root, seasons=seasons)
                if players_path.exists() else {"records": [], "quarantine": [], "source_paths": [],
                                                "game_coverage": {}, "audit": {"provider": "none"}})
    scan = scan_snapshots(snapshot_root, seasons, pbp_game_coverage=provider["game_coverage"],
                          additional_source_paths=provider["source_paths"])
    legacy_plays, play_paths = _load_play_records(snapshot_root, seasons)
    raw_plays = provider["records"] if provider["records"] else legacy_plays
    normalized, event_quarantine = normalize_events(raw_plays)
    ledgers = build_ledgers(normalized)
    historical, historical_quarantine, accepted_events = _historical_reconciliation(
        ledgers, scan["player_games"], normalized,
    )
    if len(accepted_events) != len(normalized):
        ledgers = build_ledgers(accepted_events)
    all_quarantine = [*provider["quarantine"], *event_quarantine, *ledgers["quarantine"], *historical_quarantine]
    reasons = dict(sorted(Counter(row["reason_code"] for row in all_quarantine).items()))
    required_inventory_fields = {
        "canonical_game_id", "provider_game_id", "season", "week", "kickoff_utc",
        "canonical_player_id", "provider_player_id", "canonical_team_id", "targets",
        "receptions", "receiving_yards", "rush_attempts", "rushing_yards",
    }
    inventory_fields = {row["canonical_field_name"] for row in scan["inventory"]["fields"]}
    milestone_0_acceptance = (required_inventory_fields <= inventory_fields
                              and scan["coverage_summary"]["total_games"] > 0
                              and scan["coverage_summary"]["total_player_games"] > 0)
    reconciliation = {
        "schema_version": SCHEMA_VERSION, "network_contacted": False,
        "canonical_event_reconciliation": ledgers["reconciliation"],
        "historical_outcome_reconciliation": historical,
        "quarantine_count": len(all_quarantine), "quarantine_by_reason": reasons,
        "accepted_canonical_rows_have_zero_unresolved_accounting_violations": (
            ledgers["reconciliation"]["accepted_rows_unresolved_accounting_violations"] == 0
        ),
        "milestone_0_acceptance": milestone_0_acceptance,
        "milestone_1_acceptance": bool(historical["milestone_1_historical_acceptance"] and raw_plays),
        "milestone_1_blocker": None if raw_plays else "MISSING_PLAY_BY_PLAY",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    json_artifacts = {
        "data_inventory.json": scan["inventory"], "data_inventory.schema.json": scan["schema"],
        "canonical_stat_definitions.json": scan["definitions"], "data_coverage_summary.json": scan["coverage_summary"],
        "canonical_event.schema.json": canonical_event_schema(),
        "team_game_dropback_ledger.json": ledgers["team_game_dropback_ledger"],
        "team_game_pass_attempt_allocation_ledger.json": ledgers["team_game_pass_attempt_allocation_ledger"],
        "player_game_target_reception_ledger.json": ledgers["player_game_target_reception_ledger"],
        "team_game_rush_partition_ledger.json": ledgers["team_game_rush_partition_ledger"],
        "player_game_rushing_ledger.json": ledgers["player_game_rushing_ledger"],
        "reconciliation_summary.json": reconciliation,
        "provider_ingestion_audit.json": provider["audit"],
    }
    for name, value in json_artifacts.items(): write_json(output_dir / name, value)
    write_json_gzip(output_dir / "canonical_play_events.json.gz", ledgers["accepted_events"])
    coverage_fields = list(scan["coverage_rows"][0]) if scan["coverage_rows"] else []
    anomaly_fields = ["provider", "season", "week", "game_id", "record_key", "reason_code", "detail",
                      "affects_launch_market_outputs"]
    write_csv(output_dir / "data_coverage_by_season.csv", scan["coverage_rows"], coverage_fields)
    write_csv(output_dir / "data_quality_anomalies.csv", scan["anomalies"], anomaly_fields)
    write_csv(output_dir / "quarantine.csv", all_quarantine, QUARANTINE_FIELDS)
    write_text(output_dir / "canonical_stat_definitions.md", _definitions_markdown(scan["definitions"]))
    write_text(output_dir / "milestone_0_audit.md", _audit_markdown(scan["coverage_summary"]))
    artifact_paths = sorted([path for path in output_dir.iterdir()
                             if path.is_file() and path.name != "artifact_manifest.json"],
                            key=lambda path: path.name)
    inputs = sorted(set(scan["source_paths"] + play_paths))
    manifest = {
        "schema_version": SCHEMA_VERSION, "workflow": "nfl-system-a-m0-m1-v1", "network_contacted": False,
        "sportsbook_inputs_consumed": False, "seasons": list(seasons),
        "input_hashes": {path.as_posix(): file_hash(path) for path in inputs},
        "artifact_hashes": {path.name: file_hash(path) for path in artifact_paths},
        "schema_versions": {"inventory": 1, "canonical_event": 1, "ledger": 1, "manifest": 1},
        "acceptance": {"milestone_0": reconciliation["milestone_0_acceptance"],
                       "milestone_1": reconciliation["milestone_1_acceptance"],
                       "milestone_1_blocker": reconciliation["milestone_1_blocker"]},
    }
    write_json(output_dir / "artifact_manifest.json", manifest)
    return {**json_artifacts, "canonical_play_events.json.gz": ledgers["accepted_events"],
            "artifact_manifest.json": manifest,
            "data_coverage_by_season.csv": scan["coverage_rows"],
            "data_quality_anomalies.csv": scan["anomalies"], "quarantine.csv": all_quarantine}


def verify_directories(left: Path, right: Path) -> dict[str, Any]:
    left_files = {path.name: file_hash(path) for path in left.iterdir() if path.is_file()}
    right_files = {path.name: file_hash(path) for path in right.iterdir() if path.is_file()}
    return {"deterministic": left_files == right_files, "left": left_files, "right": right_files,
            "differences": sorted(name for name in set(left_files) | set(right_files)
                                  if left_files.get(name) != right_files.get(name))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inventory", "coverage", "events", "ledgers", "audit", "verify", "all"),
                        nargs="?", default="all")
    parser.add_argument("--snapshot-root", type=Path, default=Path("backtesting/data/snapshots"))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--pbp-root", type=Path, default=DEFAULT_PBP_ROOT)
    parser.add_argument("--players-path", type=Path, default=DEFAULT_PLAYERS)
    parser.add_argument("--seasons", default="2023,2024,2025")
    parser.add_argument("--compare-dir", type=Path)
    args = parser.parse_args(argv)
    if args.command == "verify":
        if args.compare_dir is None: parser.error("verify requires --compare-dir")
        result = verify_directories(args.output_dir, args.compare_dir)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["deterministic"] else 1
    build_workflow(snapshot_root=args.snapshot_root, output_dir=args.output_dir,
                   seasons=tuple(int(value) for value in args.seasons.split(",")),
                   pbp_root=args.pbp_root, players_path=args.players_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
