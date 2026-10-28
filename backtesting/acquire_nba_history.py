"""Acquire a versioned, final-score-only NBA history snapshot from ESPN."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path

from backend.app.services.nba.schedule import ESPN_SCOREBOARD, fetch_schedule
from backend.app.services.nba.season import normalize_nba_season
from backend.app.services.team_metadata import team_by_abbreviation


def acquire(seasons: list[str]) -> dict:
    games = []
    for value in seasons:
        season = normalize_nba_season(value)
        rows = fetch_schedule(
            season=season.code,
            start=date(season.start_year, 10, 1),
            end=date(season.start_year + 1, 6, 30),
            timeout=20,
            max_attempts=3,
        )
        for row in rows:
            home = team_by_abbreviation("nba", row.get("home_team"))
            away = team_by_abbreviation("nba", row.get("away_team"))
            if (row["status"] != "FINAL" or row["season_phase"] != "regular_season"
                    or row.get("home_score") is None or row.get("away_score") is None
                    or home is None or away is None):
                continue
            games.append({**row, "season": season.code,
                          "home_team": home.abbreviation, "away_team": away.abbreviation})
    games = sorted(games, key=lambda row: (row["kickoff_time"], row["game_id"]))
    body = json.dumps(games, sort_keys=True, separators=(",", ":"))
    return {
        "metadata": {
            "league": "NBA", "provider": "espn", "sourceUrl": ESPN_SCOREBOARD,
            "seasons": seasons,
            "retrievedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "selection": "verified final regular-season games between the 30 NBA franchises with both scores",
            "gameCount": len(games), "gamesSha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        },
        "games": games,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seasons", nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = acquire(args.seasons)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload["metadata"], indent=2))


if __name__ == "__main__":
    main()
