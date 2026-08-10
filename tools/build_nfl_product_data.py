"""Build compact, deterministic runtime data for the NFL web product."""

from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def build_history() -> list[dict]:
    rows: list[dict] = []
    for path in sorted((ROOT / "backtesting/data/snapshots/nfl/2025").glob("week_*/outcomes.json")):
        for game in json.loads(path.read_text(encoding="utf-8")):
            if not game.get("completed"):
                continue
            for venue, team, opponent, scored, allowed in (
                ("home", game["home_team"], game["away_team"], game["final_home_score"], game["final_away_score"]),
                ("away", game["away_team"], game["home_team"], game["final_away_score"], game["final_home_score"]),
            ):
                rows.append({
                    "season": 2025, "week": int(game["week"]), "game_id": game["game_id"],
                    "team": team, "opponent": opponent, "home_away": venue,
                    "points_for": scored, "points_against": allowed,
                    "completed_at": game["completed_at"], "data_as_of": game["data_as_of"],
                    "record_role": "completed_game_history", "is_pregame": False,
                })
    return sorted(rows, key=lambda row: (row["week"], row["game_id"], row["team"]))


def build_roster() -> list[dict]:
    source = ROOT / "backtesting/data/system_a/nflverse/players/players.csv"
    with source.open(encoding="utf-8", newline="") as handle:
        rows = [{
            "id": row["gsis_id"], "player": row["display_name"], "team": row["latest_team"],
            "position": row["position"], "positionGroup": row["position_group"],
            "status": row["status"], "jerseyNumber": row["jersey_number"], "headshot": row["headshot"],
        } for row in csv.DictReader(handle)
            if row.get("display_name") and row.get("latest_team") and row.get("position")
            and row.get("last_season") == "2026" and row.get("status") in {"ACT", "DEV", "RES"}]
    return sorted(rows, key=lambda row: (row["team"], row["position"], row["player"]))


def main() -> None:
    output = ROOT / "data"
    output.mkdir(exist_ok=True)
    (output / "nfl_team_game_history.json").write_text(
        json.dumps({"sourceSeason": 2025, "items": build_history()}, indent=2) + "\n", encoding="utf-8"
    )
    (output / "nfl_roster_2026.json").write_text(
        json.dumps({"season": 2026, "items": build_roster()}, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
