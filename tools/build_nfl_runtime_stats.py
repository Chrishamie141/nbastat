"""Build the compact NFL history used by web prediction and fantasy tools."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

STAT_MAP = {
    "passing_yards": "PASS_YDS",
    "passing_tds": "PASS_TD",
    "interceptions": "PASS_INT",
    "rushing_yards": "RUSH_YDS",
    "rushing_tds": "RUSH_TD",
    "receiving_yards": "REC_YDS",
    "receiving_tds": "REC_TD",
    "receptions": "RECEPTIONS",
}
FANTASY_POSITIONS = {"QB", "RB", "WR", "TE"}


def _identities(players_file: Path | None, roster_file: Path | None) -> dict[str, dict]:
    identities: dict[str, dict] = {}
    if players_file and players_file.exists():
        with players_file.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                name = str(row.get("display_name") or "").strip()
                position = str(row.get("position_group") or row.get("position") or "").upper()
                if name and position in FANTASY_POSITIONS:
                    identities[name] = {
                        "position": position,
                        "current_team": str(row.get("latest_team") or "").upper(),
                    }
    if roster_file and roster_file.exists():
        for row in json.loads(roster_file.read_text(encoding="utf-8")):
            name = str(row.get("player_name") or "").strip()
            position = str(row.get("position") or "").upper()
            if name and position in FANTASY_POSITIONS:
                identities[name] = {
                    "position": position,
                    "current_team": str(row.get("team") or "").upper(),
                }
    return identities


def build(
    snapshot_root: Path,
    output: Path,
    *,
    season: int,
    games: int = 5,
    players_file: Path | None = None,
    roster_file: Path | None = None,
) -> dict:
    # One record per player/game. Snapshot rows are stat categories, not games.
    history: dict[str, dict[tuple[int, str], dict]] = defaultdict(dict)
    inputs = []
    for path in sorted((snapshot_root / "nfl" / str(season)).glob("week_*/player_stats.json")):
        inputs.append(path.as_posix())
        for row in json.loads(path.read_text(encoding="utf-8")):
            values = {
                target: row.get("stats", {}).get(source)
                for source, target in STAT_MAP.items()
                if row.get("stats", {}).get(source) is not None
            }
            if not values:
                continue
            player = str(row.get("player") or row.get("player_name") or "").strip()
            key = (int(row.get("week") or 0), str(row.get("game_id") or ""))
            game = history[player].setdefault(
                key,
                {"week": key[0], "game_id": key[1], "team": str(row.get("team") or "")},
            )
            game.update(values)

    if players_file is None:
        players_file = Path("backtesting/data/system_a/nflverse/players/players.csv")
    if roster_file is None:
        candidate = snapshot_root / "nfl" / str(season + 1) / "week_01" / "roster_identities.json"
        roster_file = candidate if candidate.exists() else None
    identities = _identities(players_file, roster_file)

    players = {}
    for player, game_map in sorted(history.items()):
        identity = identities.get(player, {})
        position = identity.get("position")
        if position not in FANTASY_POSITIONS:
            continue
        rows = sorted(game_map.values(), key=lambda row: (row["week"], row["game_id"]))
        recent = rows[-games:]
        totals = {
            stat: sum(float(row.get(stat) or 0) for row in rows)
            for stat in STAT_MAP.values()
        }
        item = {
            "team": identity.get("current_team") or rows[-1]["team"],
            "last_season_team": rows[-1]["team"],
            "position": position,
            "source_season": season,
            "games": len(recent),
            "games_played": len(rows),
            "season_stats": totals,
            "game_logs": rows,
        }
        # Preserve the recent-stat array contract used by the prop predictor.
        for stat in STAT_MAP.values():
            values = [row[stat] for row in recent if stat in row]
            if values:
                item[stat] = values
        players[player] = item
    payload = {
        "schema_version": 2,
        "source_season": season,
        "max_games": games,
        "player_count": len(players),
        "inputs": inputs,
        "players": players,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", type=Path, default=Path("backtesting/data/snapshots"))
    parser.add_argument("--output", type=Path, default=Path("data/nfl_recent_player_stats.json"))
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--games", type=int, default=5)
    parser.add_argument("--players-file", type=Path)
    parser.add_argument("--roster-file", type=Path)
    args = parser.parse_args()
    report = build(
        args.snapshot_root,
        args.output,
        season=args.season,
        games=args.games,
        players_file=args.players_file,
        roster_file=args.roster_file,
    )
    print(json.dumps({key: report[key] for key in ("schema_version", "source_season", "max_games", "player_count")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
