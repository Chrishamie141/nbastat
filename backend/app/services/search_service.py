from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.app.services.team_metadata import teams_for_league

BASE_DIR = Path(__file__).resolve().parents[3]


@lru_cache(maxsize=1)
def _player_index() -> tuple[list[dict[str, Any]], int | None]:
    paths = sorted((BASE_DIR / "backtesting" / "data" / "snapshots" / "nfl").glob("*/week_*/player_identities.json"), reverse=True)
    if not paths:
        return [], None
    path = paths[0]
    rows = json.loads(path.read_text(encoding="utf-8"))
    season = int(path.parents[1].name)
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        player_id = str(row.get("canonical_player_id") or row.get("player_id") or "")
        if player_id and row.get("player_name"):
            unique[player_id] = {"type": "player", "id": player_id, "name": row["player_name"], "team": row.get("team"), "position": row.get("position"), "contextSeasonUsed": season, "fallbackUsed": season < datetime.now(timezone.utc).year}
    return list(unique.values()), season


def search_catalog(query: str, games: list[Any] | None = None) -> dict[str, Any]:
    needle = query.casefold().strip()
    teams = []
    for league in ("nfl", "nba"):
        for team in teams_for_league(league):
            haystack = " ".join(filter(None, (team.name, team.city, team.nickname, team.abbreviation))).casefold()
            if needle in haystack:
                teams.append({"type": "team", "id": team.id, "league": league, "name": team.name, "abbreviation": team.abbreviation})
    players, player_season = _player_index()
    player_results = [row for row in players if needle in row["name"].casefold() or needle == str(row["id"]).casefold()][:12]
    game_results = []
    for game in games or []:
        row = game.model_dump(mode="json") if hasattr(game, "model_dump") else game
        away = row.get("awayTeam") or {}
        home = row.get("homeTeam") or {}
        haystack = " ".join(str(value or "") for value in (row.get("id"), away.get("name"), away.get("abbreviation"), home.get("name"), home.get("abbreviation"))).casefold()
        if needle in haystack:
            game_results.append({"type": "game", "id": row.get("id"), "league": row.get("league"), "name": f"{away.get('abbreviation')} at {home.get('abbreviation')}", "status": row.get("status"), "startTimeUtc": row.get("startTimeUtc"), "href": f"/nfl/games/{row.get('id')}" if row.get("league") == "nfl" else None})
    fallback = bool(player_results) and player_season is not None and player_season < datetime.now(timezone.utc).year
    return {"query": query, "items": teams[:12] + player_results + game_results[:12], "counts": {"teams": len(teams), "players": len(player_results), "games": len(game_results)}, "playerContext": {"contextSeasonUsed": player_season, "fallbackUsed": fallback, "fallbackReason": "CURRENT_SEASON_INDEX_UNAVAILABLE" if fallback else None}}
