"""NFL data provider layer for odds, stats, injuries, weather, and fallback samples.

Live integrations are intentionally lightweight and optional. Configure API keys
with environment variables to enable real provider data:

- THE_ODDS_API_KEY: The Odds API for NFL player props and team lines.
- SPORTSDATAIO_API_KEY: SportsDataIO NFL scores/stats/injuries.
- OPENWEATHER_API_KEY: OpenWeather current weather for outdoor game venues.

When a live provider is unavailable, malformed, or returns no usable rows, the
public functions print a clear fallback message and return deterministic sample
data so the NFL parlay flow remains usable offline.
"""

from __future__ import annotations

import json
import os
from datetime import date
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

NFL_SPORT_KEY = "americanfootball_nfl"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
SPORTSDATAIO_BASE = "https://api.sportsdata.io/v3/nfl"
OPENWEATHER_BASE = "https://api.openweathermap.org/data/2.5/weather"
REQUEST_TIMEOUT = 10

PROP_MARKETS = [
    "player_pass_yds",
    "player_rush_yds",
    "player_reception_yds",
    "player_receptions",
    "player_anytime_td",
    "player_pass_tds",
    "player_pass_interceptions",
]
TEAM_MARKETS = ["h2h", "spreads", "totals"]
MARKET_TO_STAT = {
    "player_pass_yds": "PASS_YDS",
    "player_pass_tds": "PASS_TD",
    "player_pass_interceptions": "PASS_INT",
    "player_rush_yds": "RUSH_YDS",
    "player_reception_yds": "REC_YDS",
    "player_receptions": "RECEPTIONS",
    "player_anytime_td": "TD",
}
STAT_ALIASES = {
    "PassingYards": "PASS_YDS",
    "PassingTouchdowns": "PASS_TD",
    "PassingInterceptions": "PASS_INT",
    "RushingYards": "RUSH_YDS",
    "ReceivingYards": "REC_YDS",
    "Receptions": "RECEPTIONS",
    "FantasyPointsDraftKings": "FANTASY_POINTS",
}

NFL_SAMPLE_PLAYERS = [
    {"player": "Patrick Mahomes", "team": "KC", "position": "QB", "opponent": "LAC"},
    {"player": "Christian McCaffrey", "team": "SF", "position": "RB", "opponent": "SEA"},
    {"player": "Justin Jefferson", "team": "MIN", "position": "WR", "opponent": "GB"},
    {"player": "Amon-Ra St. Brown", "team": "DET", "position": "WR", "opponent": "CHI"},
    {"player": "Josh Allen", "team": "BUF", "position": "QB", "opponent": "NYJ"},
]

NFL_SAMPLE_GAMES = [
    {"game_id": "sample-kc-lac", "home_team": "KC", "away_team": "LAC", "commence_time": "sample", "venue": "GEHA Field at Arrowhead Stadium", "city": "Kansas City"},
    {"game_id": "sample-sf-sea", "home_team": "SF", "away_team": "SEA", "commence_time": "sample", "venue": "Levi's Stadium", "city": "Santa Clara"},
]

NFL_SAMPLE_LINES = {
    "Patrick Mahomes": {"PASS_YDS": [{"line": 249.5, "odds": -115, "provider": "sample"}], "PASS_TD": [{"line": 1.5, "odds": -130, "provider": "sample"}]},
    "Christian McCaffrey": {"RUSH_YDS": [{"line": 64.5, "odds": -110, "provider": "sample"}], "TD": [{"line": 0.5, "odds": -125, "provider": "sample"}]},
    "Justin Jefferson": {"REC_YDS": [{"line": 79.5, "odds": -110, "provider": "sample"}], "RECEPTIONS": [{"line": 5.5, "odds": -120, "provider": "sample"}]},
    "Amon-Ra St. Brown": {"REC_YDS": [{"line": 69.5, "odds": -110, "provider": "sample"}], "RECEPTIONS": [{"line": 6.5, "odds": +105, "provider": "sample"}]},
    "Josh Allen": {"PASS_YDS": [{"line": 239.5, "odds": -110, "provider": "sample"}], "RUSH_YDS": [{"line": 35.5, "odds": -115, "provider": "sample"}]},
}

NFL_SAMPLE_RECENT_STATS = {
    "Patrick Mahomes": {"team": "KC", "position": "QB", "PASS_YDS": [262, 245, 301, 233, 281], "PASS_TD": [2, 1, 3, 2, 2], "RUSH_YDS": [18, 27, 12, 31, 20]},
    "Christian McCaffrey": {"team": "SF", "position": "RB", "RUSH_YDS": [72, 88, 54, 91, 63], "REC_YDS": [31, 42, 18, 36, 29], "RECEPTIONS": [4, 5, 3, 4, 4], "TD": [1, 1, 0, 2, 1]},
    "Justin Jefferson": {"team": "MIN", "position": "WR", "REC_YDS": [92, 81, 104, 67, 88], "RECEPTIONS": [7, 6, 8, 5, 7], "TD": [1, 0, 1, 0, 1]},
    "Amon-Ra St. Brown": {"team": "DET", "position": "WR", "REC_YDS": [76, 94, 71, 83, 65], "RECEPTIONS": [7, 8, 6, 7, 6], "TD": [0, 1, 1, 0, 1]},
    "Josh Allen": {"team": "BUF", "position": "QB", "PASS_YDS": [244, 289, 227, 312, 261], "PASS_TD": [2, 2, 1, 3, 2], "PASS_INT": [1, 0, 1, 0, 1], "RUSH_YDS": [42, 35, 54, 29, 47]},
}

NFL_SAMPLE_INJURIES = []
NFL_SAMPLE_WEATHER = {"condition": "sample clear", "temperature_f": 65, "wind_mph": 6, "source": "sample"}


def _fetch_json(url: str, headers: dict[str, str] | None = None) -> Any:
    request = Request(url, headers=headers or {"User-Agent": "nbastat-nfl-provider/1.0"})
    with urlopen(request, timeout=REQUEST_TIMEOUT) as response:  # nosec - URLs are fixed provider endpoints.
        return json.loads(response.read().decode("utf-8"))


def _fallback(message: str) -> None:
    print(f"NFL live data unavailable: {message}. Using fallback sample data.")


def _odds_key() -> str | None:
    return os.getenv("THE_ODDS_API_KEY") or os.getenv("ODDS_API_KEY")


def _sportsdata_key() -> str | None:
    return os.getenv("SPORTSDATAIO_API_KEY") or os.getenv("SPORTS_DATA_IO_API_KEY")


def _openweather_key() -> str | None:
    return os.getenv("OPENWEATHER_API_KEY") or os.getenv("OPEN_WEATHER_API_KEY")


def _provider_get(fetcher, fallback, label: str):
    try:
        data = fetcher()
    except (HTTPError, URLError, TimeoutError, ValueError, OSError) as exc:
        _fallback(f"{label} provider failed ({exc})")
        return fallback()
    if not data:
        _fallback(f"{label} provider returned no usable rows")
        return fallback()
    return data


def _sample_player_pool(team: str | None = None) -> list[dict[str, Any]]:
    if not team:
        return list(NFL_SAMPLE_PLAYERS)
    team_key = str(team).strip().upper()
    return [player for player in NFL_SAMPLE_PLAYERS if player["team"] == team_key]


def _sample_player_props(team: str | None = None) -> dict[str, dict[str, list[dict[str, Any]]]]:
    allowed = {p["player"] for p in _sample_player_pool(team)} if team else None
    return {player: stats for player, stats in NFL_SAMPLE_LINES.items() if allowed is None or player in allowed}


def get_nfl_games() -> list[dict[str, Any]]:
    def fetch():
        key = _odds_key()
        if not key:
            raise ValueError("THE_ODDS_API_KEY is not set")
        params = urlencode({"apiKey": key, "regions": "us", "markets": "h2h", "oddsFormat": "american"})
        rows = _fetch_json(f"{ODDS_API_BASE}/sports/{NFL_SPORT_KEY}/odds/?{params}")
        games = []
        for row in rows:
            games.append({"game_id": row.get("id"), "home_team": row.get("home_team"), "away_team": row.get("away_team"), "commence_time": row.get("commence_time")})
        return games
    return _provider_get(fetch, lambda: list(NFL_SAMPLE_GAMES), "NFL games")


def get_nfl_player_props(team: str | None = None) -> dict[str, dict[str, list[dict[str, Any]]]]:
    def fetch():
        key = _odds_key()
        if not key:
            raise ValueError("THE_ODDS_API_KEY is not set")

        games = get_nfl_games()
        if not games:
            raise ValueError("no NFL games available for player prop lookup")

        props: dict[str, dict[str, list[dict[str, Any]]]] = {}
        team_key = str(team).strip().upper() if team else None
        params = urlencode({"apiKey": key, "regions": "us", "markets": ",".join(PROP_MARKETS), "oddsFormat": "american"})

        for game in games:
            event_id = game.get("game_id") or game.get("id")
            if not event_id:
                continue
            if team_key and team_key not in {str(game.get("home_team", "")).upper(), str(game.get("away_team", "")).upper()}:
                continue

            event = _fetch_json(f"{ODDS_API_BASE}/sports/{NFL_SPORT_KEY}/events/{event_id}/odds?{params}")
            for bookmaker in event.get("bookmakers", []):
                for market in bookmaker.get("markets", []):
                    stat_type = MARKET_TO_STAT.get(market.get("key"))
                    if not stat_type:
                        continue
                    for outcome in market.get("outcomes", []):
                        player = outcome.get("description") or outcome.get("name")
                        if not player:
                            continue
                        props.setdefault(player, {}).setdefault(stat_type, []).append({
                            "line": outcome.get("point"),
                            "odds": outcome.get("price"),
                            "side": outcome.get("name"),
                            "bookmaker": bookmaker.get("title"),
                            "provider": "the-odds-api",
                            "event_id": event.get("id") or event_id,
                        })
        if not props:
            raise ValueError("The Odds API event odds endpoint returned no NFL player props")
        return props
    return _provider_get(fetch, lambda: _sample_player_props(team), "NFL player props")


def get_nfl_team_lines(team: str | None = None) -> list[dict[str, Any]]:
    def fetch():
        key = _odds_key()
        if not key:
            raise ValueError("THE_ODDS_API_KEY is not set")
        params = urlencode({"apiKey": key, "regions": "us", "markets": ",".join(TEAM_MARKETS), "oddsFormat": "american"})
        rows = _fetch_json(f"{ODDS_API_BASE}/sports/{NFL_SPORT_KEY}/odds/?{params}")
        team_key = str(team).strip().upper() if team else None
        lines = []
        for game in rows:
            teams = {str(game.get("home_team", "")).upper(), str(game.get("away_team", "")).upper()}
            if team_key and team_key not in teams:
                continue
            for bookmaker in game.get("bookmakers", []):
                for market in bookmaker.get("markets", []):
                    for outcome in market.get("outcomes", []):
                        lines.append({"game_id": game.get("id"), "market": market.get("key"), "team": outcome.get("name"), "line": outcome.get("point"), "odds": outcome.get("price"), "bookmaker": bookmaker.get("title"), "provider": "the-odds-api"})
        return lines
    return _provider_get(fetch, lambda: get_team_market_placeholders(team or "NFL"), "NFL team lines")


def get_nfl_player_recent_stats(player: str | None = None, team: str | None = None, games: int = 5) -> dict[str, dict[str, Any]]:
    def fetch():
        key = _sportsdata_key()
        if not key:
            raise ValueError("SPORTSDATAIO_API_KEY is not set")
        season = date.today().year
        url = f"{SPORTSDATAIO_BASE}/stats/json/PlayerGameStatsBySeason/{season}?key={key}"
        rows = _fetch_json(url)
        grouped: dict[str, dict[str, Any]] = {}
        team_key = str(team).strip().upper() if team else None
        player_key = str(player).strip().lower() if player else None
        for row in rows:
            name = row.get("Name")
            if not name or (player_key and player_key not in name.lower()):
                continue
            if team_key and str(row.get("Team", "")).upper() != team_key:
                continue
            item = grouped.setdefault(name, {"team": row.get("Team"), "position": row.get("Position"), "games": []})
            item["games"].append(row)
        normalized = {}
        for name, item in grouped.items():
            recent = sorted(item["games"], key=lambda r: str(r.get("Day") or r.get("DateTime") or ""), reverse=True)[:games]
            normalized[name] = {"team": item.get("team"), "position": item.get("position")}
            for provider_field, stat_type in STAT_ALIASES.items():
                values = [row.get(provider_field) for row in recent if row.get(provider_field) is not None]
                if values:
                    normalized[name][stat_type] = values
        return normalized
    def sample():
        rows = {name: data for name, data in NFL_SAMPLE_RECENT_STATS.items() if (not player or player.lower() in name.lower())}
        if team:
            rows = {name: data for name, data in rows.items() if data.get("team") == team.upper()}
        return rows
    return _provider_get(fetch, sample, "NFL player recent stats")


def get_nfl_injuries(team: str | None = None) -> list[dict[str, Any]]:
    def fetch():
        key = _sportsdata_key()
        if not key:
            raise ValueError("SPORTSDATAIO_API_KEY is not set")
        season = date.today().year
        rows = _fetch_json(f"{SPORTSDATAIO_BASE}/scores/json/Injuries/{season}?key={key}")
        team_key = str(team).strip().upper() if team else None
        return [{"player": r.get("Name"), "team": r.get("Team"), "status": r.get("Status"), "body_part": r.get("BodyPart"), "notes": r.get("PracticeDescription") or r.get("InjuryNotes"), "provider": "sportsdataio"} for r in rows if not team_key or str(r.get("Team", "")).upper() == team_key]
    return _provider_get(fetch, lambda: list(NFL_SAMPLE_INJURIES), "NFL injuries")


def get_nfl_weather(game: dict[str, Any] | None = None, city: str | None = None) -> dict[str, Any]:
    def fetch():
        key = _openweather_key()
        if not key:
            raise ValueError("OPENWEATHER_API_KEY is not set")
        location = city or (game or {}).get("city") or (game or {}).get("home_team")
        if not location:
            raise ValueError("no game city supplied")
        params = urlencode({"q": location, "appid": key, "units": "imperial"})
        row = _fetch_json(f"{OPENWEATHER_BASE}?{params}")
        return {"condition": (row.get("weather") or [{}])[0].get("description"), "temperature_f": (row.get("main") or {}).get("temp"), "wind_mph": (row.get("wind") or {}).get("speed"), "source": "openweather"}
    return _provider_get(fetch, lambda: dict(NFL_SAMPLE_WEATHER), "NFL weather")


def get_nfl_player_pool(team: str | None = None) -> list[dict[str, Any]]:
    props = get_nfl_player_props(team=team)
    stats = get_nfl_player_recent_stats(team=team)
    players = {}
    for name in set(props) | set(stats):
        stat_row = stats.get(name, {})
        players[name] = {"player": name, "team": stat_row.get("team"), "position": stat_row.get("position", "WR")}
    return list(players.values()) if players else _sample_player_pool(team)


def get_nfl_lines():
    """Backward-compatible alias for normalized NFL player props."""
    return get_nfl_player_props()


def get_team_market_placeholders(team):
    team_key = str(team or "NFL").strip().upper()
    return [
        {"team": team_key, "market": "MONEYLINE", "prediction": f"{team_key} moneyline lean", "confidence": 52, "notes": "Fallback sample team market; connect real odds."},
        {"team": team_key, "market": "SPREAD", "prediction": f"{team_key} spread lean", "confidence": 51, "notes": "Fallback sample team market; connect real odds."},
        {"team": team_key, "market": "TOTAL", "prediction": "Game total lean", "confidence": 50, "notes": "Fallback sample total; connect real odds."},
    ]


def get_nfl_final_player_stats(game_id: str | None = None, week: int | None = None, season: int | None = None) -> dict[str, dict[str, Any]]:
    """Return final NFL player stats for grading, or an empty dict when unavailable.

    Placeholder for a completed-game provider such as SportsDataIO box scores.
    The empty fallback is intentional: grading leaves parlays pending instead of
    using sample/projection data as final results.
    """
    def fetch():
        key = _sportsdata_key()
        if not key:
            raise ValueError("SPORTSDATAIO_API_KEY is not set")
        raise ValueError("final NFL player stat provider is not configured")

    return _provider_get(fetch, dict, "NFL final player stats")


def get_nfl_final_team_results(game_id: str | None = None, week: int | None = None, season: int | None = None) -> dict[str, dict[str, Any]]:
    """Return final NFL team grading results, or an empty dict when unavailable.

    Expected shape by team: {"KC": {"won": True, "margin": 3, "total": 47}}.
    The empty fallback keeps pending parlays unchanged when finals are not wired.
    """
    def fetch():
        key = _sportsdata_key()
        if not key:
            raise ValueError("SPORTSDATAIO_API_KEY is not set")
        raise ValueError("final NFL team result provider is not configured")

    return _provider_get(fetch, dict, "NFL final team results")
