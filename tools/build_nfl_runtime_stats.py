"""Build the compact, outcome-only NFL history used by the web predictor."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

STAT_MAP = {
    "passing_yards": "PASS_YDS",
    "passing_tds": "PASS_TD",
    "rushing_yards": "RUSH_YDS",
    "receiving_yards": "REC_YDS",
    "receptions": "RECEPTIONS",
}


def build(snapshot_root: Path, output: Path, *, season: int, games: int = 5) -> dict:
    history = defaultdict(list)
    inputs = []
    for path in sorted((snapshot_root / "nfl" / str(season)).glob("week_*/player_stats.json")):
        inputs.append(path.as_posix())
        for row in json.loads(path.read_text(encoding="utf-8")):
            values = {target: row.get("stats", {}).get(source) for source, target in STAT_MAP.items()
                      if row.get("stats", {}).get(source) is not None}
            if values:
                history[str(row["player"])].append((int(row.get("week") or 0), str(row.get("game_id") or ""),
                                                    str(row.get("team") or ""), values))
    players = {}
    for player, rows in sorted(history.items()):
        recent = sorted(rows, key=lambda row: (row[0], row[1]))[-games:]
        item = {"team": recent[-1][2], "source_season": season, "games": len(recent)}
        for _week, _game_id, _team, stats in recent:
            for stat, value in stats.items():
                item.setdefault(stat, []).append(value)
        players[player] = item
    payload = {"schema_version": 1, "source_season": season, "max_games": games,
               "player_count": len(players), "players": players}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", type=Path, default=Path("backtesting/data/snapshots"))
    parser.add_argument("--output", type=Path, default=Path("data/nfl_recent_player_stats.json"))
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--games", type=int, default=5)
    args = parser.parse_args()
    report = build(args.snapshot_root, args.output, season=args.season, games=args.games)
    print(json.dumps({key: report[key] for key in ("schema_version", "source_season", "max_games", "player_count")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
