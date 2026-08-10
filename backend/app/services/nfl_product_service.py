"""Week-first NFL product services with immutable pregame prediction snapshots."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from time import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from backend.app.database import get_db_connection, using_postgres
from backtesting.nfl_game_predictor import NFLGameMarketPredictorV2, V2_MODEL_VERSION, no_vig_probabilities
from nfl_data_service import NFL_TEAM_ABBREVIATIONS, get_nfl_team_lines
from nfl_fantasy_service import build_fantasy_rankings

ROOT = Path(__file__).resolve().parents[3]
HISTORY_PATH = ROOT / "data/nfl_team_game_history.json"
ROSTER_PATH = ROOT / "data/nfl_roster_2026.json"
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
ESPN_SCHEDULE_CDN = "https://cdn.espn.com/core/nfl/schedule"
PROFILE_MINIMUM = {"SAFE": 0.62, "BALANCED": 0.57, "AGGRESSIVE": 0.52}
DEPTH_SLOTS = ["QB", "RB", "WR1", "WR2", "WR3", "TE", "LT", "LG", "C", "RG", "RT",
               "EDGE1", "DT1", "DT2", "EDGE2", "LB1", "LB2", "CB1", "CB2", "S1", "S2", "FLEX"]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _abbr(value: str | None) -> str:
    text = str(value or "").strip()
    return NFL_TEAM_ABBREVIATIONS.get(text, text.upper())


@lru_cache(maxsize=64)
def _schedule(season: int, week: int) -> list[dict]:
    query = urlencode({"dates": season, "seasontype": 2, "week": week, "limit": 100})
    cdn_query = urlencode({"xhr": 1, "year": season, "week": week, "seasontype": 2})
    payload = None
    last_error = None
    for url in (f"{ESPN_SCOREBOARD}?{query}", f"{ESPN_SCHEDULE_CDN}?{cdn_query}"):
        request = Request(url, headers={"User-Agent": "Mozilla/5.0 SmartBetSports/2.0", "Accept": "application/json"})
        try:
            with urlopen(request, timeout=12) as response:  # nosec - fixed ESPN endpoints
                payload = json.loads(response.read().decode("utf-8"))
            break
        except Exception as exc:
            last_error = exc
    if payload is None:
        raise RuntimeError("All verified NFL schedule providers failed") from last_error

    events = payload.get("events")
    if events is None:
        schedule = payload.get("content", {}).get("schedule", {})
        events = [game for day in schedule.values() for game in day.get("games", [])]
    games = []
    for event in events:
        competition = (event.get("competitions") or [{}])[0]
        teams = {row.get("homeAway"): row for row in competition.get("competitors", [])}
        if not teams.get("home") or not teams.get("away"):
            continue
        status = event.get("status", {}).get("type", {})
        games.append({
            "game_id": f"espn-{event['id']}", "season": season, "week": week,
            "kickoff_time": event.get("date"), "home_team": teams["home"]["team"].get("abbreviation"),
            "away_team": teams["away"]["team"].get("abbreviation"),
            "home_name": teams["home"]["team"].get("displayName"),
            "away_name": teams["away"]["team"].get("displayName"),
            "home_score": int(teams["home"].get("score") or 0), "away_score": int(teams["away"].get("score") or 0),
            "venue": competition.get("venue", {}).get("fullName"),
            "broadcast": [row.get("names", [None])[0] for row in competition.get("broadcasts", []) if row.get("names")],
            "status": "final" if status.get("completed") else "live" if status.get("state") == "in" else "scheduled",
        })
    return sorted(games, key=lambda game: (game["kickoff_time"] or "", game["game_id"]))


def _history() -> list[dict]:
    return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))["items"]


@lru_cache(maxsize=4)
def _odds_by_game(cache_window: int) -> dict[frozenset[str], list[dict]]:
    try:
        rows = [row for row in get_nfl_team_lines() if row.get("provider") == "the-odds-api"]
    except Exception:
        rows = []
    grouped: dict[frozenset[str], list[dict]] = {}
    for row in rows:
        key = frozenset({_abbr(row.get("home_team")), _abbr(row.get("away_team"))})
        if len(key) == 2:
            grouped.setdefault(key, []).append(row)
    return grouped


def _moneyline(rows: list[dict], home: str, away: str) -> dict:
    quotes = [row for row in rows if row.get("market") == "h2h" and row.get("odds") not in (None, 0)]
    by_book: dict[str, dict[str, dict]] = {}
    for row in quotes:
        team = _abbr(row.get("team"))
        if team in {home, away}:
            by_book.setdefault(str(row.get("bookmaker")), {})[team] = row
    pairs = [pair for pair in by_book.values() if home in pair and away in pair]
    if not pairs:
        return {"homeOdds": None, "awayOdds": None, "homeImpliedProbability": None, "awayImpliedProbability": None, "sportsbook": None}
    pair = sorted(pairs, key=lambda value: str(value[home].get("bookmaker")))[0]
    probabilities = no_vig_probabilities([float(pair[home]["odds"]), float(pair[away]["odds"])])
    return {"homeOdds": pair[home]["odds"], "awayOdds": pair[away]["odds"],
            "homeImpliedProbability": round(probabilities[0], 4), "awayImpliedProbability": round(probabilities[1], 4),
            "sportsbook": pair[home].get("bookmaker")}


def _initialize_predictions() -> None:
    identifier = "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY" if using_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    with get_db_connection() as connection:
        connection.execute(f"""CREATE TABLE IF NOT EXISTS nfl_game_predictions (
            id {identifier}, user_id BIGINT NOT NULL, game_id TEXT NOT NULL, season INTEGER NOT NULL,
            week INTEGER NOT NULL, kickoff_time TEXT NOT NULL, generated_at TEXT NOT NULL,
            model_version TEXT NOT NULL, prediction_json TEXT NOT NULL,
            UNIQUE(user_id, game_id, model_version))""")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_nfl_game_prediction_owner ON nfl_game_predictions(user_id, season, week)")
        if using_postgres():
            connection.execute("ALTER TABLE nfl_game_predictions ENABLE ROW LEVEL SECURITY")


def _save_prediction(user_id: int, game: dict, prediction: dict) -> None:
    if game["status"] != "scheduled" or datetime.fromisoformat(game["kickoff_time"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
        return
    _initialize_predictions()
    with get_db_connection() as connection:
        exists = connection.execute(
            "SELECT 1 FROM nfl_game_predictions WHERE user_id=? AND game_id=? AND model_version=?",
            (user_id, game["game_id"], V2_MODEL_VERSION),
        ).fetchone()
        if not exists:
            connection.execute("""INSERT INTO nfl_game_predictions
                (user_id,game_id,season,week,kickoff_time,generated_at,model_version,prediction_json)
                VALUES (?,?,?,?,?,?,?,?)""", (user_id, game["game_id"], game["season"], game["week"],
                game["kickoff_time"], _now(), V2_MODEL_VERSION, json.dumps(prediction, sort_keys=True)))


def weekly_board(season: int, week: int, profile: str, user_id: int, day: str | None = None) -> dict:
    profile = str(profile or "BALANCED").upper()
    if profile not in PROFILE_MINIMUM:
        raise ValueError("profile must be SAFE, BALANCED, or AGGRESSIVE")
    schedule = _schedule(season, week)
    odds = _odds_by_game(int(time() // 300))
    predictor, history = NFLGameMarketPredictorV2(), _history()
    items = []
    for game in schedule:
        projection = predictor.project(game, history)
        market = _moneyline(odds.get(frozenset({game["home_team"], game["away_team"]}), []), game["home_team"], game["away_team"])
        item = {**game, "profile": profile, "modelVersion": V2_MODEL_VERSION, "market": market,
                "predictionStatus": "available" if projection else "unavailable", "recommended": False}
        if projection:
            home_probability = projection.probability("h2h", game["home_team"], home_team=game["home_team"], away_team=game["away_team"])
            winner = game["home_team"] if home_probability >= 0.5 else game["away_team"]
            probability = home_probability if winner == game["home_team"] else 1 - home_probability
            implied = market["homeImpliedProbability"] if winner == game["home_team"] else market["awayImpliedProbability"]
            prediction = {"winner": winner, "winProbability": round(probability, 4),
                          "confidence": round(projection.confidence, 1),
                          "projectedHomeScore": round(projection.home_points, 1),
                          "projectedAwayScore": round(projection.away_points, 1),
                          "edge": round(probability - implied, 4) if implied is not None else None,
                          "dataAsOf": projection.data_as_of, "modelVersion": projection.model_version}
            item.update(prediction)
            item["recommended"] = probability >= PROFILE_MINIMUM[profile]
            _save_prediction(user_id, game, prediction)
        if day and day.upper() != "ALL":
            weekday = datetime.fromisoformat(game["kickoff_time"].replace("Z", "+00:00")).strftime("%A").upper()
            if weekday != day.upper():
                continue
        items.append(item)
    return {"season": season, "week": week, "profile": profile, "items": items,
            "gameCount": len(items), "recommendedCount": sum(bool(row["recommended"]) for row in items),
            "modelVersion": V2_MODEL_VERSION, "generatedAt": _now(),
            "methodology": "Pregame-only 2025 completed-game history; profile changes recommendations, never schedule coverage."}


def historical_games(season: int, user_id: int) -> list[dict]:
    _initialize_predictions()
    snapshots = {}
    with get_db_connection() as connection:
        for row in connection.execute("SELECT game_id, generated_at, model_version, prediction_json FROM nfl_game_predictions WHERE user_id=? AND season=?", (user_id, season)).fetchall():
            snapshots[row["game_id"]] = {**json.loads(row["prediction_json"]), "generatedAt": row["generated_at"], "modelVersion": row["model_version"]}
    completed = []
    for week in range(1, 19):
        try:
            games = _schedule(season, week)
        except Exception:
            continue
        for game in games:
            if game["status"] == "final":
                snapshot = snapshots.get(game["game_id"])
                actual = game["home_team"] if game["home_score"] > game["away_score"] else game["away_team"]
                completed.append({**game, "actualWinner": actual, "prediction": snapshot,
                                  "predictionResult": None if not snapshot else "hit" if snapshot["winner"] == actual else "miss"})
    return sorted(completed, key=lambda game: game["kickoff_time"], reverse=True)


def _initialize_depth_charts() -> None:
    identifier = "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY" if using_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    with get_db_connection() as connection:
        connection.execute(f"""CREATE TABLE IF NOT EXISTS fantasy_depth_charts (
            id {identifier}, user_id BIGINT NOT NULL, name TEXT NOT NULL, formation_json TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_fantasy_depth_chart_owner ON fantasy_depth_charts(user_id, updated_at)")
        if using_postgres():
            connection.execute("ALTER TABLE fantasy_depth_charts ENABLE ROW LEVEL SECURITY")


def fantasy_depth_chart_data(user_id: int, scoring: str = "PPR") -> dict:
    roster = json.loads(ROSTER_PATH.read_text(encoding="utf-8"))["items"]
    ranks = build_fantasy_rankings(scoring, "ALL", 100)["items"]
    rank_by_name = {row["player"]: row for row in ranks}
    players = [{**row, "fantasy": rank_by_name.get(row["player"])} for row in roster]
    return {"season": 2026, "scoring": scoring, "slots": DEPTH_SLOTS, "players": players,
            "savedCharts": list_depth_charts(user_id), "dataMode": "verified_roster_and_prior_season_model"}


def list_depth_charts(user_id: int) -> list[dict]:
    _initialize_depth_charts()
    with get_db_connection() as connection:
        rows = connection.execute("SELECT id,name,formation_json,created_at,updated_at FROM fantasy_depth_charts WHERE user_id=? ORDER BY updated_at DESC", (user_id,)).fetchall()
    return [{"id": row["id"], "name": row["name"], "formation": json.loads(row["formation_json"]),
             "createdAt": row["created_at"], "updatedAt": row["updated_at"]} for row in rows]


def save_depth_chart(user_id: int, name: str, formation: dict, chart_id: int | None = None) -> dict:
    _initialize_depth_charts(); timestamp = _now(); name = str(name or "My Depth Chart").strip()[:80]
    allowed = set(DEPTH_SLOTS)
    cleaned = {slot: str(player_id) for slot, player_id in formation.items() if slot in allowed and player_id}
    with get_db_connection() as connection:
        if chart_id is None:
            if using_postgres():
                chart_id = connection.execute("INSERT INTO fantasy_depth_charts (user_id,name,formation_json,created_at,updated_at) VALUES (?,?,?,?,?) RETURNING id",
                                              (user_id, name, json.dumps(cleaned, sort_keys=True), timestamp, timestamp)).fetchone()["id"]
            else:
                cursor = connection.execute("INSERT INTO fantasy_depth_charts (user_id,name,formation_json,created_at,updated_at) VALUES (?,?,?,?,?)",
                                            (user_id, name, json.dumps(cleaned, sort_keys=True), timestamp, timestamp))
                chart_id = cursor.lastrowid
        else:
            cursor = connection.execute("UPDATE fantasy_depth_charts SET name=?,formation_json=?,updated_at=? WHERE id=? AND user_id=?",
                                        (name, json.dumps(cleaned, sort_keys=True), timestamp, chart_id, user_id))
            if cursor.rowcount != 1:
                raise LookupError("Depth chart not found")
    return next(row for row in list_depth_charts(user_id) if int(row["id"]) == int(chart_id))


def delete_depth_chart(user_id: int, chart_id: int) -> None:
    _initialize_depth_charts()
    with get_db_connection() as connection:
        cursor = connection.execute("DELETE FROM fantasy_depth_charts WHERE id=? AND user_id=?", (chart_id, user_id))
        if cursor.rowcount != 1:
            raise LookupError("Depth chart not found")
