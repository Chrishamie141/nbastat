"""Frozen nflverse play-by-play adapter for the canonical System A contract."""
from __future__ import annotations

import csv
import gzip
import json
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

from ..game_matching import normalize_team
from .artifacts import file_hash
from .events import quarantine


def _flag(value: Any) -> bool:
    return str(value or "").strip() in {"1", "1.0", "true", "True"}


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _team(value: Any) -> str:
    normalized = normalize_team(value)
    return "LAR" if normalized == "LA" else normalized


def _load_players(path: Path) -> tuple[dict[str, dict[str, str]], list[dict[str, Any]]]:
    players: dict[str, dict[str, str]] = {}
    findings = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row_number, row in enumerate(csv.DictReader(handle), start=2):
            gsis = str(row.get("gsis_id") or "").strip()
            espn = str(row.get("espn_id") or "").strip()
            if not gsis or not espn:
                continue
            value = {"canonical_player_id": espn, "position": str(row.get("position") or "").strip()}
            if gsis in players and players[gsis] != value:
                findings.append(quarantine(
                    {"provider_name": "nflverse", "source_reference": f"{path.name}:{row_number}",
                     "gsis_id": gsis, "existing": players[gsis], "conflicting": value},
                    "UNRESOLVED_PLAYER_ID", "GSIS identifier has conflicting ESPN crosswalk rows",
                ))
                players.pop(gsis, None)
            elif gsis not in players:
                players[gsis] = value
    return players, findings


def _load_games(snapshot_root: Path, seasons: Sequence[int]) -> tuple[dict[tuple[int, int, str, str], str], list[dict[str, Any]]]:
    games: dict[tuple[int, int, str, str], str] = {}
    findings = []
    for season in seasons:
        for path in sorted((snapshot_root / "nfl" / str(season)).glob("week_*/games.json")):
            try:
                rows = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for row in rows if isinstance(rows, list) else []:
                key = (season, int(row.get("week") or 0), _team(row.get("away_team")), _team(row.get("home_team")))
                game_id = str(row.get("game_id") or "")
                if not game_id or key in games:
                    findings.append(quarantine(
                        {"provider_name": "espn-scoreboard", "season": season, "week": key[1],
                         "game_id": game_id, "source_reference": path.as_posix(), "mapping_key": key},
                        "UNRESOLVED_TEAM_ID", "canonical schedule game mapping is missing or ambiguous",
                    ))
                    games.pop(key, None)
                else:
                    games[key] = game_id
    return games, findings


def load_nflverse_events(*, raw_root: Path, players_path: Path, snapshot_root: Path,
                         seasons: Sequence[int]) -> dict[str, Any]:
    """Translate frozen nflverse CSV releases into identity-resolved provider records."""
    players, findings = _load_players(players_path)
    games, game_findings = _load_games(snapshot_root, seasons)
    findings.extend(game_findings)
    records = []
    source_paths = [players_path]
    games_seen: dict[tuple[int, int], set[str]] = {}
    counters = Counter()

    for season in seasons:
        path = raw_root / f"play_by_play_{season}.csv.gz"
        if not path.exists():
            findings.append(quarantine(
                {"provider_name": "nflverse", "season": season, "source_reference": path.as_posix()},
                "SOURCE_RECORD_MALFORMED", "frozen nflverse season file is missing",
            ))
            continue
        source_paths.append(path)
        digest = file_hash(path)
        with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
            for row_number, row in enumerate(csv.DictReader(handle), start=2):
                if row.get("season_type") != "REG":
                    continue
                week = int(_number(row.get("week")))
                if week < 1 or week > 18:
                    continue
                parts = str(row.get("game_id") or "").split("_")
                if len(parts) < 4:
                    continue
                key = (season, week, _team(parts[-2]), _team(parts[-1]))
                canonical_game_id = games.get(key)
                if not canonical_game_id:
                    findings.append(quarantine(
                        {"provider_name": "nflverse", "season": season, "week": week,
                         "provider_play_id": row.get("play_id"), "source_reference": f"{path.name}:{row_number}",
                         "provider_game_id": row.get("game_id"), "mapping_key": key},
                        "UNRESOLVED_TEAM_ID", "nflverse game does not map uniquely to the frozen ESPN schedule",
                    ))
                    continue
                games_seen.setdefault((season, week), set()).add(canonical_game_id)
                play_type = str(row.get("play_type") or "")
                pass_attempt = _flag(row.get("pass_attempt"))
                rush_attempt = _flag(row.get("rush_attempt"))
                no_play = play_type == "no_play"
                if not (pass_attempt or rush_attempt or no_play or _flag(row.get("sack"))):
                    continue

                gsis_ids = {role: str(row.get(field) or "").strip() for role, field in (
                    ("passer", "passer_player_id"), ("target", "receiver_player_id"),
                    ("rusher", "rusher_player_id"),
                )}
                mapped = {role: players.get(value, {}).get("canonical_player_id") if value else None
                          for role, value in gsis_ids.items()}
                required_roles = []
                if pass_attempt and gsis_ids["target"]:
                    required_roles.append("target")
                if rush_attempt and gsis_ids["rusher"]:
                    required_roles.append("rusher")
                unresolved = [role for role in required_roles if not mapped[role]]
                if unresolved:
                    findings.append(quarantine(
                        {"provider_name": "nflverse", "season": season, "week": week,
                         "canonical_game_id": canonical_game_id, "provider_play_id": row.get("play_id"),
                         "canonical_offense_team_id": _team(row.get("posteam")),
                         "source_reference": f"{path.name}:{row_number}", "unresolved_roles": unresolved,
                         "raw_player_ids": gsis_ids},
                        "UNRESOLVED_PLAYER_ID", "credited nflverse participant has no unique ESPN ID crosswalk",
                    ))
                    continue

                if no_play:
                    event_type = "OTHER"
                elif _flag(row.get("sack")):
                    event_type = "SACK"
                elif _flag(row.get("qb_scramble")):
                    event_type = "SCRAMBLE"
                elif play_type == "qb_spike":
                    event_type = "SPIKE"
                elif pass_attempt:
                    event_type = ("INTERCEPTION" if _flag(row.get("interception")) else
                                  "COMPLETION" if _flag(row.get("complete_pass")) else "PASS")
                elif _flag(row.get("qb_kneel")):
                    event_type = "KNEEL"
                elif rush_attempt and not gsis_ids["rusher"]:
                    event_type = "TEAM_RUSH"
                elif rush_attempt:
                    event_type = ("QB_RUSH" if players.get(gsis_ids["rusher"], {}).get("position") == "QB" else "RUSH")
                else:
                    continue

                lateral = any(_flag(row.get(field)) for field in ("lateral_reception", "lateral_rush"))
                record = {
                    "season": season, "week": week, "canonical_game_id": canonical_game_id,
                    "provider_game_id": row.get("game_id"), "provider_play_id": str(row.get("play_id")),
                    "play_id": f"nflverse:{row.get('game_id')}:{row.get('play_id')}", "provider_name": "nflverse",
                    "source_artifact_hash": digest, "raw_record_reference": f"{path.name}:{row_number}",
                    "canonical_offense_team_id": _team(row.get("posteam")),
                    "canonical_defense_team_id": _team(row.get("defteam")),
                    "quarter": int(_number(row.get("qtr"))), "clock": row.get("time") or None,
                    "play_sequence": int(_number(row.get("play_id"))), "event_type": event_type,
                    "quarterback_id": mapped["passer"] or (mapped["rusher"] if event_type in {"SCRAMBLE", "KNEEL", "QB_RUSH"} else None),
                    "passer_id": mapped["passer"], "target_player_id": mapped["target"],
                    "receiver_id": mapped["target"] if _flag(row.get("complete_pass")) else None,
                    "rusher_id": mapped["rusher"], "completed_pass": _flag(row.get("complete_pass")),
                    "interception": _flag(row.get("interception")), "no_play": no_play,
                    "nullified_by_penalty": no_play, "two_point_attempt": _flag(row.get("two_point_attempt")),
                    "aborted_play": _flag(row.get("aborted_play")), "lateral_indicator": lateral,
                    "lateral_semantics_resolved": not lateral,
                    "receiving_yards": _number(row.get("receiving_yards")),
                    "rushing_yards": _number(row.get("rushing_yards")),
                    "sack_yards": abs(_number(row.get("yards_gained"))) if event_type == "SACK" else 0.0,
                }
                if not record["canonical_offense_team_id"] or not record["canonical_defense_team_id"]:
                    if no_play:
                        counters["SKIPPED_NON_OFFENSIVE_NO_PLAY"] += 1
                        continue
                    findings.append(quarantine(record, "UNRESOLVED_TEAM_ID", "launch event lacks offense/defense identity"))
                    continue
                records.append(record)
                counters[event_type] += 1
    return {
        "records": records, "quarantine": findings, "source_paths": source_paths,
        "game_coverage": {key: len(value) for key, value in sorted(games_seen.items())},
        "audit": {"provider": "nflverse", "records_emitted": len(records),
                  "event_types": dict(sorted(counters.items())), "games_seen": sum(len(value) for value in games_seen.values()),
                  "source_hashes": {path.name: file_hash(path) for path in source_paths},
                  "source_urls": {
                      **{f"play_by_play_{season}.csv.gz":
                         f"https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.csv.gz"
                         for season in seasons},
                      "players.csv": "https://github.com/nflverse/nflverse-data/releases/download/players/players.csv",
                  },
                  "license": "CC-BY-4.0", "attribution": "nflverse and contributors"},
    }
