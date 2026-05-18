from __future__ import annotations

from os import getenv
from statistics import mean
from typing import Any

import requests
from dotenv import load_dotenv

ODDS_API_URL = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
ODDS_MARKETS = "spreads,totals,h2h"
ODDS_REGION = "us"
ODDS_FORMAT = "american"
MISSING_API_KEY_MESSAGE = (
    "The Odds API key is missing. Add THE_ODDS_API_KEY to .env to enable market comparison."
)
ODDS_FALLBACK_MESSAGE = "Sportsbook odds are unavailable right now; market comparison skipped."

TEAM_ALIASES = {
    "ATL": "Atlanta Hawks",
    "BOS": "Boston Celtics",
    "BKN": "Brooklyn Nets",
    "CHA": "Charlotte Hornets",
    "CHI": "Chicago Bulls",
    "CLE": "Cleveland Cavaliers",
    "DAL": "Dallas Mavericks",
    "DEN": "Denver Nuggets",
    "DET": "Detroit Pistons",
    "GSW": "Golden State Warriors",
    "HOU": "Houston Rockets",
    "IND": "Indiana Pacers",
    "LAC": "LA Clippers",
    "LAL": "Los Angeles Lakers",
    "MEM": "Memphis Grizzlies",
    "MIA": "Miami Heat",
    "MIL": "Milwaukee Bucks",
    "MIN": "Minnesota Timberwolves",
    "NOP": "New Orleans Pelicans",
    "NYK": "New York Knicks",
    "OKC": "Oklahoma City Thunder",
    "ORL": "Orlando Magic",
    "PHI": "Philadelphia 76ers",
    "PHX": "Phoenix Suns",
    "POR": "Portland Trail Blazers",
    "SAC": "Sacramento Kings",
    "SAS": "San Antonio Spurs",
    "TOR": "Toronto Raptors",
    "UTA": "Utah Jazz",
    "WAS": "Washington Wizards",
}


def _safe_average(values: list[float]) -> float | None:
    return round(mean(values), 1) if values else None


def _team_name(team: str) -> str:
    team = team.upper().strip()
    return TEAM_ALIASES.get(team, team)


def fetch_nba_odds(timeout: int = 10) -> dict[str, Any]:
    """Fetch NBA market odds from The Odds API with safe fallbacks."""
    load_dotenv()
    api_key = getenv("THE_ODDS_API_KEY")

    if not api_key:
        return {"ok": False, "message": MISSING_API_KEY_MESSAGE, "games": []}

    params = {
        "apiKey": api_key,
        "regions": ODDS_REGION,
        "markets": ODDS_MARKETS,
        "oddsFormat": ODDS_FORMAT,
    }

    try:
        response = requests.get(ODDS_API_URL, params=params, timeout=timeout)
        response.raise_for_status()
        return {"ok": True, "message": None, "games": response.json()}
    except Exception as exc:
        return {"ok": False, "message": f"{ODDS_FALLBACK_MESSAGE} ({exc})", "games": []}


def _find_market(bookmaker: dict[str, Any], key: str) -> dict[str, Any] | None:
    for market in bookmaker.get("markets", []):
        if market.get("key") == key:
            return market
    return None


def aggregate_game_odds(game: dict[str, Any]) -> dict[str, Any]:
    """Aggregate sportsbook lines for a single game response."""
    spread_lines: dict[str, list[dict[str, Any]]] = {}
    total_lines: list[dict[str, Any]] = []
    h2h_lines: dict[str, list[dict[str, Any]]] = {}
    sportsbooks: list[str] = []

    for bookmaker in game.get("bookmakers", []):
        sportsbook = bookmaker.get("title") or bookmaker.get("key") or "Unknown"
        included = False

        spreads = _find_market(bookmaker, "spreads")
        if spreads:
            for outcome in spreads.get("outcomes", []):
                team = outcome.get("name")
                point = outcome.get("point")
                if team is None or point is None:
                    continue
                spread_lines.setdefault(team, []).append({
                    "sportsbook": sportsbook,
                    "point": float(point),
                    "price": outcome.get("price"),
                })
                included = True

        totals = _find_market(bookmaker, "totals")
        if totals:
            for outcome in totals.get("outcomes", []):
                point = outcome.get("point")
                side = outcome.get("name")
                if point is None or side not in {"Over", "Under"}:
                    continue
                total_lines.append({
                    "sportsbook": sportsbook,
                    "side": side,
                    "point": float(point),
                    "price": outcome.get("price"),
                })
                included = True

        h2h = _find_market(bookmaker, "h2h")
        if h2h:
            for outcome in h2h.get("outcomes", []):
                team = outcome.get("name")
                if team is None:
                    continue
                h2h_lines.setdefault(team, []).append({
                    "sportsbook": sportsbook,
                    "price": outcome.get("price"),
                })
                included = True

        if included and sportsbook not in sportsbooks:
            sportsbooks.append(sportsbook)

    average_spread_by_team = {
        team: _safe_average([line["point"] for line in lines])
        for team, lines in spread_lines.items()
    }
    best_spread_by_team = {
        team: max(lines, key=lambda line: line["point"])
        for team, lines in spread_lines.items()
        if lines
    }
    total_points = [line["point"] for line in total_lines]
    over_points = [line for line in total_lines if line["side"] == "Over"]
    under_points = [line for line in total_lines if line["side"] == "Under"]

    return {
        "id": game.get("id"),
        "commence_time": game.get("commence_time"),
        "home_team": game.get("home_team"),
        "away_team": game.get("away_team"),
        "sportsbooks": sportsbooks,
        "average_spread_by_team": average_spread_by_team,
        "best_spread_by_team": best_spread_by_team,
        "average_total": _safe_average(total_points),
        "best_total": {
            "over": min(over_points, key=lambda line: line["point"]) if over_points else None,
            "under": max(under_points, key=lambda line: line["point"]) if under_points else None,
        },
        "h2h": h2h_lines,
    }


def find_game_odds(team1_abbr: str, team2_abbr: str, games: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return aggregated odds for the matching NBA game, if present."""
    wanted = {_team_name(team1_abbr), _team_name(team2_abbr)}

    for game in games:
        teams = {game.get("home_team"), game.get("away_team")}
        if wanted == teams:
            return aggregate_game_odds(game)

    return None
