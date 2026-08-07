"""Build compact, deterministic catalogs bundled with the production API."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build() -> dict[str, int]:
    team_rows: dict[tuple[str, str], dict] = {}
    for path in sorted((ROOT / "backtesting/data/snapshots/nfl/2025").glob("week_*/team_stats.json")):
        for row in json.loads(path.read_text(encoding="utf-8")):
            key = (str(row.get("game_id") or ""), str(row.get("team") or ""))
            if all(key):
                team_rows[key] = row

    identity_path = ROOT / "backtesting/data/snapshots/nfl/2026/week_01/player_identities.json"
    players: dict[str, dict] = {}
    for row in json.loads(identity_path.read_text(encoding="utf-8")):
        player_id = str(row.get("canonical_player_id") or row.get("player_id") or "")
        if player_id and row.get("player_name"):
            players[player_id] = {
                "id": player_id,
                "name": row["player_name"],
                "team": row.get("team"),
                "position": row.get("position"),
                "season": 2026,
            }

    data_dir = ROOT / "data"
    (data_dir / "nfl_team_context_history.json").write_text(json.dumps(list(team_rows.values()), separators=(",", ":"), sort_keys=True) + "\n", encoding="utf-8")
    (data_dir / "nfl_player_search_index.json").write_text(json.dumps(list(players.values()), separators=(",", ":"), sort_keys=True) + "\n", encoding="utf-8")
    return {"teamRows": len(team_rows), "players": len(players)}


if __name__ == "__main__":
    print(json.dumps(build(), sort_keys=True))
