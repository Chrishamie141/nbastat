"""Week-first NFL product services with immutable pregame prediction snapshots."""

from __future__ import annotations

import json
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from math import erf, sqrt
from functools import lru_cache
from pathlib import Path
from time import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from backend.app.database import column_exists, get_db_connection, using_postgres
from backtesting.nfl_game_predictor import NFLGameMarketPredictorV2, V2_MODEL_VERSION, no_vig_probabilities
from nfl_data_service import NFL_TEAM_ABBREVIATIONS, fetch_nfl_team_lines_live
from nfl_fantasy_service import build_fantasy_rankings
from models import DifficultyLevel, Parlay, ParlayLeg, ParlayResult, SportType

ROOT = Path(__file__).resolve().parents[3]
HISTORY_PATH = ROOT / "data/nfl_team_game_history.json"
ROSTER_PATH = ROOT / "data/nfl_roster_2026.json"
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
ESPN_SCHEDULE_CDN = "https://cdn.espn.com/core/nfl/schedule"
PROFILE_POLICY = {
    "SAFE": {"minimum_probability": 0.62, "minimum_edge": 0.02},
    "BALANCED": {"minimum_probability": 0.57, "minimum_edge": 0.01},
    "AGGRESSIVE": {"minimum_probability": 0.52, "minimum_edge": 0.03},
}
SEASON_TYPES = {"preseason": 1, "regular": 2, "postseason": 3}
SCHEDULE_REFRESH_SECONDS = 300
MAPPING_REFRESH_SECONDS = 3600
DEPTH_SLOTS = ["QB", "RB", "WR1", "WR2", "WR3", "TE", "LT", "LG", "C", "RG", "RT",
               "EDGE1", "DT1", "DT2", "EDGE2", "LB1", "LB2", "CB1", "CB2", "S1", "S2", "FLEX"]
logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _abbr(value: str | None) -> str:
    text = str(value or "").strip()
    return NFL_TEAM_ABBREVIATIONS.get(text, text.upper())


def _season_type(value: str | None) -> str:
    normalized = str(value or "regular").strip().lower().replace("-", "_")
    aliases = {
        "pre": "preseason", "regular_season": "regular", "reg": "regular",
        "post": "postseason", "playoffs": "postseason", "post_season": "postseason",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in SEASON_TYPES:
        raise ValueError("season_type must be preseason, regular, or postseason")
    return normalized


def _score(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def nfl_season_year(at: datetime | None = None) -> int:
    """Resolve the NFL season year without tying the product to a calendar constant."""
    instant = at or datetime.now(timezone.utc)
    return instant.year - 1 if instant.month <= 2 else instant.year


@lru_cache(maxsize=512)
def _provider_schedule_cached(season: int, provider_week: int, season_type: str,
                              refresh_window: int) -> list[dict]:
    """Fetch one provider week. Provider numbering must never escape this boundary."""
    season_type = _season_type(season_type)
    provider_type = SEASON_TYPES[season_type]
    query = urlencode({"dates": season, "seasontype": provider_type, "week": provider_week, "limit": 100})
    cdn_query = urlencode({"xhr": 1, "year": season, "week": provider_week, "seasontype": provider_type})
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
        try:
            from backend.app.services.nfl_experiment_service import record_schedule_refresh
            record_schedule_refresh(provider="espn", season=season, season_type=season_type,
                                    provider_week=provider_week, refreshed_at=_now(), state="UPSTREAM_ERROR",
                                    game_count=0, safe_error_code=type(last_error).__name__)
        except Exception:
            logger.exception("schedule_refresh_logging_failed provider=espn")
        raise RuntimeError("All verified NFL schedule providers failed") from last_error

    events = payload.get("events")
    if events is None:
        schedule = payload.get("content", {}).get("schedule", {})
        events = [game for day in schedule.values() for game in day.get("games", [])]
    games_by_id = {}
    for event in events:
        competition = (event.get("competitions") or [{}])[0]
        teams = {row.get("homeAway"): row for row in competition.get("competitors", [])}
        if not teams.get("home") or not teams.get("away"):
            continue
        status = event.get("status", {}).get("type", {})
        game = {
            "game_id": f"espn-{event['id']}", "season": season, "provider_week": provider_week,
            "season_type": season_type,
            "kickoff_time": event.get("date"), "home_team": teams["home"]["team"].get("abbreviation"),
            "away_team": teams["away"]["team"].get("abbreviation"),
            "home_name": teams["home"]["team"].get("displayName"),
            "away_name": teams["away"]["team"].get("displayName"),
            "home_score": _score(teams["home"].get("score")), "away_score": _score(teams["away"].get("score")),
            "venue": competition.get("venue", {}).get("fullName"),
            "broadcast": [row.get("names", [None])[0] for row in competition.get("broadcasts", []) if row.get("names")],
            "status": "final" if status.get("completed") else "live" if status.get("state") == "in" else "scheduled",
        }
        games_by_id[game["game_id"]] = game
    games = sorted(games_by_id.values(), key=lambda game: (game["kickoff_time"] or "", game["game_id"]))
    try:
        from backend.app.services.nfl_experiment_service import record_schedule_refresh
        record_schedule_refresh(provider="espn", season=season, season_type=season_type,
                                provider_week=provider_week, refreshed_at=_now(), state="HEALTHY",
                                game_count=len(games))
    except Exception:
        logger.exception("schedule_refresh_logging_failed provider=espn")
    return games


def _provider_schedule(season: int, provider_week: int, season_type: str = "regular") -> list[dict]:
    return _provider_schedule_cached(
        season, provider_week, _season_type(season_type), int(time() // SCHEDULE_REFRESH_SECONDS)
    )


_provider_schedule.cache_clear = _provider_schedule_cached.cache_clear


@lru_cache(maxsize=32)
def _preseason_week_mapping_cached(season: int, refresh_window: int) -> dict[int, dict]:
    """Derive user-facing slates from schedule shape, independently of ESPN labels."""
    provider_slates = [(provider_week, _provider_schedule(season, provider_week, "preseason"))
                       for provider_week in range(1, 7)]
    nonempty = [(provider_week, games) for provider_week, games in provider_slates if games]
    if not nonempty:
        return {}
    mapping: dict[int, dict] = {}
    has_hof = len(nonempty[0][1]) == 1 and any(len(games) >= 8 for _, games in nonempty[1:])
    display_week = 0
    for index, (provider_week, _) in enumerate(nonempty):
        is_hof = has_hof and index == 0
        if not is_hof:
            display_week += 1
        canonical_week = 0 if is_hof else display_week
        mapping[canonical_week] = {
            "provider_week": provider_week,
            "week_key": "HOF" if is_hof else f"PRE{canonical_week}",
            "week_label": "Hall of Fame Game" if is_hof else f"Preseason Week {canonical_week}",
        }
    return mapping


def _preseason_week_mapping(season: int) -> dict[int, dict]:
    return _preseason_week_mapping_cached(season, int(time() // MAPPING_REFRESH_SECONDS))


_preseason_week_mapping.cache_clear = _preseason_week_mapping_cached.cache_clear


def _schedule(season: int, week: int, season_type: str = "regular") -> list[dict]:
    """Return a canonical display week; week is never a provider week."""
    season_type = _season_type(season_type)
    if season_type == "preseason":
        identity = _preseason_week_mapping(season).get(week)
        if not identity:
            return []
    elif season_type == "regular":
        identity = {"provider_week": week, "week_key": f"REG{week}", "week_label": f"Week {week}"}
    else:
        postseason_labels = {
            1: "Wild Card", 2: "Divisional Round", 3: "Conference Championships",
            4: "Pro Bowl", 5: "Super Bowl",
        }
        identity = {"provider_week": week, "week_key": f"POST{week}",
                    "week_label": postseason_labels.get(week, f"Postseason Week {week}")}
    games = _provider_schedule(season, identity["provider_week"], season_type)
    return [{**game, "week": week, "display_week": week, "provider": "espn",
             "week_key": identity["week_key"], "week_label": identity["week_label"]}
            for game in games]


def _history() -> list[dict]:
    return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))["items"]


@lru_cache(maxsize=4)
def _odds_by_game(cache_window: int) -> dict[frozenset[str], list[dict]]:
    from backend.app.services.nfl_experiment_service import record_provider_attempt

    rows, health = fetch_nfl_team_lines_live()
    rows = [row for row in rows if row.get("provider") == "the-odds-api"]
    verified_rows = rows if health["state"] == "HEALTHY" else []
    record_provider_attempt(
        provider="the-odds-api", state=health["state"], attempted_at=health["attempted_at"],
        completed_at=health.get("completed_at"), safe_error_code=health.get("safe_error_code"),
        http_status=health.get("http_status"), market_count=len(verified_rows),
        last_market_timestamp=health.get("last_market_timestamp"),
    )
    grouped: dict[frozenset[str], list[dict]] = {}
    for row in verified_rows:
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
    def overround(value: dict[str, dict]) -> tuple[float, str]:
        prices = [float(value[home]["odds"]), float(value[away]["odds"])]
        raw = [(-price / (-price + 100) if price < 0 else 100 / (price + 100)) for price in prices]
        return abs(sum(raw) - 1.0), str(value[home].get("bookmaker"))
    pair = min(pairs, key=overround)
    probabilities = no_vig_probabilities([float(pair[home]["odds"]), float(pair[away]["odds"])])
    return {"homeOdds": pair[home]["odds"], "awayOdds": pair[away]["odds"],
            "homeImpliedProbability": round(probabilities[0], 4), "awayImpliedProbability": round(probabilities[1], 4),
            "sportsbook": pair[home].get("bookmaker")}


def _market_context(rows: list[dict], home: str, away: str) -> dict:
    """Preserve auditable coverage for every team market without inventing a line."""
    moneyline = _moneyline(rows, home, away)
    books = sorted({str(row.get("bookmaker")) for row in rows if row.get("bookmaker")})
    timestamps = sorted({str(row.get("last_update")) for row in rows if row.get("last_update")})
    spreads = [row for row in rows if row.get("market") == "spreads" and _abbr(row.get("team")) == home]
    totals = [row for row in rows if row.get("market") == "totals" and str(row.get("team", "")).lower() == "over"]
    spread = min(spreads, key=lambda row: abs(float(row.get("odds") or -110) + 110), default=None)
    total = min(totals, key=lambda row: abs(float(row.get("odds") or -110) + 110), default=None)
    return {**moneyline,
            "provider": "the-odds-api" if rows else None,
            "homeSpread": None if not spread else spread.get("line"),
            "spreadOdds": None if not spread else spread.get("odds"),
            "total": None if not total else total.get("line"),
            "overOdds": None if not total else total.get("odds"),
            "bookmakerCount": len(books), "bookmakers": books,
            "marketTimestamp": timestamps[-1] if timestamps else None,
            "coverage": {
                "moneyline": moneyline["homeOdds"] is not None and moneyline["awayOdds"] is not None,
                "spread": spread is not None, "total": total is not None,
                "playerProps": "not_queried",
            }}


def _preseason_history(season: int, week: int) -> list[dict]:
    """Build same-season, completed preseason observations known before this week."""
    rows = []
    for prior_week in range(0, week):
        for game in _schedule(season, prior_week, "preseason"):
            if game["status"] != "final" or game["home_score"] is None or game["away_score"] is None:
                continue
            for side, opponent, venue in (("home_team", "away_team", "home"), ("away_team", "home_team", "away")):
                is_home = side == "home_team"
                rows.append({
                    "team": game[side], "opponent": game[opponent], "home_away": venue,
                    "points_for": game["home_score"] if is_home else game["away_score"],
                    "points_against": game["away_score"] if is_home else game["home_score"],
                    "completed_at": game["kickoff_time"], "game_id": game["game_id"],
                })
    return rows


def _preseason_projection(game: dict, history: list[dict]) -> dict | None:
    """Conservative current-preseason score model with explicit personnel uncertainty."""
    by_team: dict[str, list[dict]] = {}
    for row in history:
        by_team.setdefault(_abbr(row.get("team")), []).append(row)
    home_rows, away_rows = by_team.get(game["home_team"], []), by_team.get(game["away_team"], [])
    if not home_rows or not away_rows:
        return None
    all_points = [float(row["points_for"]) for row in history]
    league_points = sum(all_points) / len(all_points) if all_points else 20.0

    def form(rows: list[dict]) -> tuple[float, float, float]:
        games = len(rows)
        prior_games = 2.0
        scored = (sum(float(row["points_for"]) for row in rows) + league_points * prior_games) / (games + prior_games)
        allowed = (sum(float(row["points_against"]) for row in rows) + league_points * prior_games) / (games + prior_games)
        return scored, allowed, scored - allowed

    home_for, home_against, home_net = form(home_rows)
    away_for, away_against, away_net = form(away_rows)
    home_points = (home_for + away_against) / 2
    away_points = (away_for + home_against) / 2
    expected_margin = home_points - away_points
    raw_home = .5 * (1 + erf(expected_margin / (18.0 * sqrt(2))))
    # Missing rotations, starter availability, and snap plans make extreme preseason
    # probabilities indefensible. Shrink rather than pretending those inputs exist.
    home_probability = .5 + (raw_home - .5) * .65
    home_probability = min(.64, max(.36, home_probability))
    minimum_games = min(len(home_rows), len(away_rows))
    evidence_score = min(55.0, 35.0 + minimum_games * 10.0)
    return {
        "home_probability": home_probability, "home_points": home_points, "away_points": away_points,
        "evidence_score": evidence_score, "data_as_of": max(str(row["completed_at"]) for row in home_rows + away_rows),
        "model_version": "nfl_preseason_score_v1", "minimum_games": minimum_games,
        "home_net": home_net, "away_net": away_net,
    }


def _risk_level(probability: float, evidence_score: float, season_type: str) -> str:
    if season_type == "regular" and probability >= .62 and evidence_score >= 60:
        return "SAFE"
    if probability >= .57 and evidence_score >= 45:
        return "BALANCED"
    return "AGGRESSIVE"


def _explanation(*, season_type: str, winner: str, probability: float, projection: dict) -> tuple[list[str], str, list[str]]:
    if season_type == "preseason":
        stronger = winner
        reasons = [
            f"{stronger} has the stronger current-preseason scoring profile in the verified results available before kickoff.",
            "Regular-season power ratings and star-player production are excluded from this preseason estimate.",
        ]
        risk = "Verified starter availability, quarterback rotation, and expected playing time are unavailable; second-half roster usage can overturn this lean."
        missing = ["starter availability", "QB rotation", "expected playing time", "verified game-specific injuries"]
    else:
        reasons = [
            f"The score model gives {winner} the higher win probability ({probability * 100:.1f}%).",
            "The estimate uses completed pre-kickoff team results and does not use the current game's outcome.",
        ]
        risk = "Late injuries, lineup changes, and market movement are not included unless a verified provider supplied them."
        missing = ["verified late lineup changes", "game-specific injury impact"]
    return reasons, risk, missing


def _initialize_predictions() -> None:
    identifier = "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY" if using_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    with get_db_connection() as connection:
        connection.execute(f"""CREATE TABLE IF NOT EXISTS nfl_game_predictions (
            id {identifier}, user_id BIGINT NOT NULL, game_id TEXT NOT NULL, season INTEGER NOT NULL,
            week INTEGER NOT NULL, kickoff_time TEXT NOT NULL, generated_at TEXT NOT NULL,
            model_version TEXT NOT NULL, prediction_json TEXT NOT NULL,
            UNIQUE(user_id, game_id, model_version))""")
        additions = {
            "season_type": "TEXT", "display_week": "INTEGER", "provider_week": "INTEGER",
            "provider": "TEXT", "week_key": "TEXT",
        }
        for column, definition in additions.items():
            if not column_exists(connection, "nfl_game_predictions", column):
                connection.execute(f"ALTER TABLE nfl_game_predictions ADD COLUMN {column} {definition}")
        legacy = connection.execute(
            "SELECT id,week,prediction_json FROM nfl_game_predictions WHERE display_week IS NULL"
        ).fetchall()
        for row in legacy:
            try:
                payload = json.loads(row["prediction_json"])
            except (TypeError, ValueError):
                payload = {}
            legacy_type = _season_type(payload.get("seasonType"))
            provider_week = int(row["week"])
            display_week = max(0, provider_week - 1) if legacy_type == "preseason" else provider_week
            week_key = ("HOF" if display_week == 0 else f"PRE{display_week}") if legacy_type == "preseason" else f"REG{display_week}"
            connection.execute(
                "UPDATE nfl_game_predictions SET season_type=?,display_week=?,provider_week=?,provider=?,week_key=? WHERE id=?",
                (legacy_type, display_week, provider_week, "espn", week_key, row["id"]),
            )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_nfl_game_prediction_owner ON nfl_game_predictions(user_id, season, week)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_nfl_game_prediction_canonical ON nfl_game_predictions(user_id, season, season_type, display_week)")
        connection.execute(f"""CREATE TABLE IF NOT EXISTS nfl_market_observations (
            id {identifier}, observation_key TEXT NOT NULL UNIQUE, game_id TEXT NOT NULL,
            season INTEGER NOT NULL, season_type TEXT NOT NULL, display_week INTEGER NOT NULL,
            provider_week INTEGER, kickoff_time TEXT NOT NULL, retrieved_at TEXT NOT NULL,
            market_timestamp TEXT, provider TEXT NOT NULL, market_json TEXT NOT NULL)""")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_nfl_market_observation_game "
            "ON nfl_market_observations(game_id, retrieved_at)"
        )
        prediction_rows = connection.execute(
            "SELECT game_id,season,season_type,display_week,provider_week,kickoff_time,"
            "generated_at,prediction_json FROM nfl_game_predictions"
        ).fetchall()
        for row in prediction_rows:
            try:
                payload = json.loads(row["prediction_json"])
                market = payload.get("market") or {}
            except (TypeError, ValueError):
                continue
            if not any((market.get("coverage") or {}).get(name) for name in ("moneyline", "spread", "total")):
                continue
            _insert_market_observation(
                connection,
                game_id=row["game_id"], season=row["season"],
                season_type=row["season_type"] or payload.get("seasonType") or "regular",
                display_week=row["display_week"] if row["display_week"] is not None else payload.get("displayWeek"),
                provider_week=row["provider_week"] if row["provider_week"] is not None else payload.get("providerWeek"),
                kickoff_time=row["kickoff_time"], retrieved_at=row["generated_at"],
                market=market,
            )
        if using_postgres():
            connection.execute("ALTER TABLE nfl_game_predictions ENABLE ROW LEVEL SECURITY")
            connection.execute("ALTER TABLE nfl_market_observations ENABLE ROW LEVEL SECURITY")


def _insert_market_observation(connection, *, game_id: str, season: int, season_type: str,
                               display_week: int, provider_week: int | None, kickoff_time: str,
                               retrieved_at: str, market: dict) -> bool:
    from backend.app.services.nfl_experiment_service import is_strictly_pregame

    if not is_strictly_pregame(
        market_timestamp=market.get("marketTimestamp"), retrieved_at=retrieved_at,
        kickoff_timestamp=kickoff_time,
    ):
        logger.warning("market_observation_rejected boundary=kickoff game=%s", game_id)
        return False
    serialized = json.dumps(market, sort_keys=True, separators=(",", ":"))
    fingerprint = json.dumps({key: value for key, value in market.items() if key != "marketTimestamp"},
                             sort_keys=True, separators=(",", ":"))
    previous = connection.execute(
        "SELECT market_json FROM nfl_market_observations WHERE game_id=? ORDER BY retrieved_at DESC,id DESC LIMIT 1",
        (game_id,),
    ).fetchone()
    if previous:
        previous_market = json.loads(previous["market_json"])
        previous_fingerprint = json.dumps(
            {key: value for key, value in previous_market.items() if key != "marketTimestamp"},
            sort_keys=True, separators=(",", ":"),
        )
        if previous_fingerprint == fingerprint:
            logger.info("market_capture game=%s inserted=false reason=unchanged_payload", game_id)
            return False
    observation_key = hashlib.sha256(f"{game_id}|{serialized}".encode("utf-8")).hexdigest()
    cursor = connection.execute(
        """INSERT INTO nfl_market_observations
        (observation_key,game_id,season,season_type,display_week,provider_week,kickoff_time,
         retrieved_at,market_timestamp,provider,market_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(observation_key) DO NOTHING""",
        (observation_key, game_id, season, season_type, display_week, provider_week,
         kickoff_time, retrieved_at, market.get("marketTimestamp"), "the-odds-api", serialized),
    )
    inserted = bool(cursor.rowcount)
    logger.info("market_capture game=%s inserted=%s provider=the-odds-api", game_id, inserted)
    return inserted


def _save_market_observation(game: dict, market: dict) -> None:
    coverage = market.get("coverage") or {}
    if not any(coverage.get(name) is True for name in ("moneyline", "spread", "total")):
        return
    from backend.app.services.nfl_experiment_service import is_strictly_pregame, operational_kickoff

    retrieved_at = _now()
    kickoff_time = operational_kickoff(game["game_id"], game["kickoff_time"])
    if not is_strictly_pregame(market_timestamp=market.get("marketTimestamp"),
                               retrieved_at=retrieved_at, kickoff_timestamp=kickoff_time):
        logger.warning("market_capture_skipped reason=not_strictly_pregame game=%s", game["game_id"])
        return
    _initialize_predictions()
    with get_db_connection() as connection:
        _insert_market_observation(
            connection, game_id=game["game_id"], season=game["season"],
            season_type=game["season_type"], display_week=game["display_week"],
            provider_week=game.get("provider_week"), kickoff_time=kickoff_time,
            retrieved_at=retrieved_at, market=market,
        )


def _market_history(game_id: str) -> dict:
    from backend.app.services.nfl_experiment_service import market_history_for_game

    _initialize_predictions()
    return market_history_for_game(game_id)


def _price_dependent_fields(prediction: dict, game: dict, market: dict, profile: str) -> dict:
    winner = prediction.get("winner")
    probability = float(prediction.get("winProbability") or 0)
    implied = (market.get("homeImpliedProbability") if winner == game["home_team"]
               else market.get("awayImpliedProbability"))
    if implied is None and market.get("homeOdds") not in (None, 0) and market.get("awayOdds") not in (None, 0):
        probabilities = no_vig_probabilities([
            float(market["homeOdds"]), float(market["awayOdds"])
        ])
        implied = probabilities[0] if winner == game["home_team"] else probabilities[1]
    edge = round(probability - float(implied), 4) if implied is not None else None
    eligibility = {
        name: (implied is not None and probability >= policy["minimum_probability"]
               and edge is not None and edge >= policy["minimum_edge"])
        for name, policy in PROFILE_POLICY.items()
    }
    policy = PROFILE_POLICY[profile]
    if implied is None:
        reason = "verified_price_unavailable"
    elif probability < policy["minimum_probability"]:
        reason = "probability_below_profile_minimum"
    elif edge is None or edge < policy["minimum_edge"]:
        reason = "price_too_expensive"
    else:
        reason = "profile_threshold_met"
    return {
        "market": market, "edge": edge, "profileEligibility": eligibility,
        "recommendedBet": eligibility[profile], "recommended": eligibility[profile],
        "recommendationReason": reason,
        "modelRating": prediction.get("rating"),
        "ratingBasis": "frozen_model_generation_context",
    }


def _save_prediction(user_id: int, game: dict, prediction: dict) -> None:
    if game["status"] != "scheduled" or datetime.fromisoformat(game["kickoff_time"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
        return
    _initialize_predictions()
    model_version = str(prediction.get("modelVersion") or V2_MODEL_VERSION)
    season_type = _season_type(game.get("season_type"))
    display_week = int(game.get("display_week", game["week"]))
    provider_week = int(game.get("provider_week", display_week + 1 if season_type == "preseason" else display_week))
    week_key = game.get("week_key") or ("HOF" if season_type == "preseason" and display_week == 0
                                        else f"PRE{display_week}" if season_type == "preseason" else f"REG{display_week}")
    with get_db_connection() as connection:
        connection.execute("""INSERT INTO nfl_game_predictions
            (user_id,game_id,season,week,kickoff_time,generated_at,model_version,prediction_json,
             season_type,display_week,provider_week,provider,week_key)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id,game_id,model_version) DO NOTHING""",
            (user_id, game["game_id"], game["season"], game["week"], game["kickoff_time"],
             _now(), model_version, json.dumps(prediction, sort_keys=True), season_type,
             display_week, provider_week, game.get("provider", "espn"), week_key))


def _prediction_snapshots(user_id: int, season: int, week: int | None = None,
                          season_type: str | None = None) -> dict[str, dict]:
    _initialize_predictions()
    query = "SELECT game_id, generated_at, model_version, prediction_json FROM nfl_game_predictions WHERE user_id=? AND season=?"
    params: tuple = (user_id, season)
    if week is not None:
        query += " AND display_week=?"
        params += (week,)
    if season_type is not None:
        query += " AND season_type=?"
        params += (_season_type(season_type),)
    snapshots = {}
    with get_db_connection() as connection:
        for row in connection.execute(query, params).fetchall():
            snapshots[row["game_id"]] = {
                **json.loads(row["prediction_json"]), "generatedAt": row["generated_at"],
                "modelVersion": row["model_version"],
            }
    return snapshots


def weekly_board(season: int, week: int, profile: str, user_id: int, day: str | None = None,
                 season_type: str = "regular") -> dict:
    profile = str(profile or "BALANCED").upper()
    if profile not in PROFILE_POLICY:
        raise ValueError("profile must be SAFE, BALANCED, or AGGRESSIVE")
    season_type = _season_type(season_type)
    schedule = _schedule(season, week, season_type)
    odds = _odds_by_game(int(time() // 300))
    predictor, history = NFLGameMarketPredictorV2(), _history()
    preseason_history = _preseason_history(season, week) if season_type == "preseason" else []
    user_snapshots = _prediction_snapshots(user_id, season, week, season_type)
    system_snapshots = user_snapshots if user_id == 0 else _prediction_snapshots(0, season, week, season_type)
    snapshots = {**user_snapshots, **system_snapshots}
    items = []
    for raw_game in schedule:
        game = {**raw_game}
        game.setdefault("display_week", week)
        game.setdefault("provider_week", week + 1 if season_type == "preseason" else week)
        game.setdefault("provider", "espn")
        game.setdefault("week_key", "HOF" if season_type == "preseason" and week == 0 else
                        f"PRE{week}" if season_type == "preseason" else f"REG{week}")
        game.setdefault("week_label", "Hall of Fame Game" if game["week_key"] == "HOF" else
                        f"Preseason Week {week}" if season_type == "preseason" else f"Week {week}")
        from backend.app.services.nfl_experiment_service import record_schedule_game
        record_schedule_game(game)
        market = _market_context(odds.get(frozenset({game["home_team"], game["away_team"]}), []), game["home_team"], game["away_team"])
        if game["status"] == "scheduled":
            _save_market_observation(game, market)
        market_history = _market_history(game["game_id"])
        latest_market = (market_history.get("latest") or {}).get("market")
        effective_market = market if any((market.get("coverage") or {}).get(name) is True
                                         for name in ("moneyline", "spread", "total")) else latest_market or market
        item = {**game, "profile": profile, "modelVersion": V2_MODEL_VERSION, "market": market,
                "marketHistory": market_history,
                "predictionStatus": "unavailable", "recommended": False, "recommendedBet": False}

        snapshot = snapshots.get(game["game_id"])
        if game["status"] != "scheduled":
            if snapshot:
                item.update(snapshot)
                item["predictionStatus"] = "pregame_snapshot"
                item["market"] = snapshot.get("market") or market
                if game["status"] == "final" and game["home_score"] is not None and game["away_score"] is not None:
                    actual = None if game["home_score"] == game["away_score"] else game["home_team"] if game["home_score"] > game["away_score"] else game["away_team"]
                    item["actualWinner"] = actual
                    item["predictionResult"] = "push" if actual is None else "hit" if snapshot.get("winner") == actual else "miss"
                    eligibility = snapshot.get("profileEligibility") or {}
                    item["betGrade"] = ("NO_BET" if not eligibility.get(profile) else
                                        "PUSH" if actual is None else "WIN" if snapshot.get("winner") == actual else "LOSS")
            else:
                item["unavailableReason"] = "No stored pregame prediction exists. Final games are never re-predicted after the outcome is known."
            if day and day.upper() != "ALL":
                weekday = datetime.fromisoformat(game["kickoff_time"].replace("Z", "+00:00")).strftime("%A").upper()
                if weekday != day.upper():
                    continue
            items.append(item)
            continue

        if snapshot:
            item.update(snapshot)
            item["predictionStatus"] = "pregame_snapshot"
            item["frozenModelGeneratedAt"] = snapshot.get("generatedAt")
            item["frozenMarketAtModelGeneration"] = snapshot.get("market")
            item["frozenProfileEligibility"] = snapshot.get("profileEligibility")
            has_effective_price = (
                effective_market.get("homeOdds") not in (None, 0)
                and effective_market.get("awayOdds") not in (None, 0)
            )
            snapshot_market = effective_market if has_effective_price else snapshot.get("market") or effective_market
            item.update(_price_dependent_fields(snapshot, game, snapshot_market, profile))
            item["marketHistory"] = market_history
            if day and day.upper() != "ALL":
                weekday = datetime.fromisoformat(game["kickoff_time"].replace("Z", "+00:00")).strftime("%A").upper()
                if weekday != day.upper():
                    continue
            items.append(item)
            continue

        if season_type == "preseason":
            raw_projection = _preseason_projection(game, preseason_history)
            projection_data = raw_projection
        else:
            projection = predictor.project(game, history)
            projection_data = None if projection is None else {
                "home_probability": projection.probability("h2h", game["home_team"], home_team=game["home_team"], away_team=game["away_team"]),
                "home_points": projection.home_points, "away_points": projection.away_points,
                "evidence_score": projection.confidence, "data_as_of": projection.data_as_of,
                "model_version": projection.model_version,
            }
        if projection_data:
            home_probability = projection_data["home_probability"]
            winner = game["home_team"] if home_probability >= 0.5 else game["away_team"]
            probability = home_probability if winner == game["home_team"] else 1 - home_probability
            implied = market["homeImpliedProbability"] if winner == game["home_team"] else market["awayImpliedProbability"]
            home_probability_display = round(home_probability, 4)
            edge = round(probability - implied, 4) if implied is not None else None
            evidence_score = round(projection_data["evidence_score"], 1)
            reasons, main_risk, missing_data = _explanation(
                season_type=season_type, winner=winner, probability=probability, projection=projection_data,
            )
            risk_level = _risk_level(probability, evidence_score, season_type)
            rating = round(min(9.5, max(5.0, 5.0 + (probability - .5) * 20 + max(edge or 0, 0) * 10)), 1)
            eligibility = {name: (implied is not None and probability >= policy["minimum_probability"]
                                   and edge is not None and edge >= policy["minimum_edge"])
                           for name, policy in PROFILE_POLICY.items()}
            prediction = {"winner": winner, "winProbability": round(probability, 4),
                          "homeWinProbability": home_probability_display,
                          "awayWinProbability": round(1 - home_probability_display, 4),
                          "evidenceScore": evidence_score, "confidence": evidence_score,
                          "confidenceMetric": "evidence_score_not_probability",
                          "projectedHomeScore": round(projection_data["home_points"], 1),
                          "projectedAwayScore": round(projection_data["away_points"], 1),
                          "edge": edge, "riskLevel": risk_level, "rating": rating,
                          "reasons": reasons, "mainRisk": main_risk, "missingData": missing_data,
                          "dataAsOf": projection_data["data_as_of"], "modelVersion": projection_data["model_version"],
                          "seasonType": season_type, "displayWeek": game["display_week"],
                          "providerWeek": game["provider_week"], "provider": game["provider"],
                          "weekKey": game["week_key"], "weekLabel": game["week_label"],
                          "profileEligibility": eligibility, "market": market}
            item.update(prediction)
            item["predictionStatus"] = "available"
            item["recommendedBet"] = eligibility[profile]
            item["recommended"] = item["recommendedBet"]
            item["recommendationReason"] = _price_dependent_fields(prediction, game, market, profile)["recommendationReason"]
            item["marketHistory"] = _market_history(game["game_id"])
            _save_prediction(0, game, prediction)
            if user_id != 0:
                _save_prediction(user_id, game, prediction)
        else:
            item["unavailableReason"] = (
                "Insufficient completed current-preseason history for both teams; no regular-season rating was substituted."
                if season_type == "preseason" else
                "Insufficient verified pregame team history for both teams."
            )
        if day and day.upper() != "ALL":
            weekday = datetime.fromisoformat(game["kickoff_time"].replace("Z", "+00:00")).strftime("%A").upper()
            if weekday != day.upper():
                continue
        items.append(item)
    identity = ({"weekKey": "HOF", "weekLabel": "Hall of Fame Game"} if season_type == "preseason" and week == 0
                else {"weekKey": f"PRE{week}", "weekLabel": f"Preseason Week {week}"} if season_type == "preseason"
                else {"weekKey": f"REG{week}", "weekLabel": f"Week {week}"})
    kickoff_times = [row["kickoff_time"] for row in schedule if row.get("kickoff_time")]
    return {"season": season, "week": week, "displayWeek": week, **identity, "profile": profile, "items": items,
            "seasonType": season_type,
            "startDate": min(kickoff_times) if kickoff_times else None,
            "endDate": max(kickoff_times) if kickoff_times else None,
            "gameCount": len(items), "recommendedCount": sum(bool(row["recommended"]) for row in items),
            "modelVersion": "nfl_preseason_score_v1" if season_type == "preseason" else V2_MODEL_VERSION,
            "generatedAt": _now(),
            "methodology": (
                "Current-season completed preseason scores only, shrunk toward league average; personnel uncertainty limits extreme probabilities."
                if season_type == "preseason" else
                "Pregame-only completed-game history; profile changes bet thresholds, never schedule coverage."
            ),
            "recommendationPolicy": PROFILE_POLICY[profile]}


def historical_games(season: int, user_id: int, season_type: str = "regular") -> list[dict]:
    season_type = _season_type(season_type)
    user_snapshots = _prediction_snapshots(user_id, season, season_type=season_type)
    system_snapshots = user_snapshots if user_id == 0 else _prediction_snapshots(0, season, season_type=season_type)
    snapshots = {**user_snapshots, **system_snapshots}
    completed = []
    for week in range(0 if season_type == "preseason" else 1, 6 if season_type == "preseason" else 19):
        try:
            games = _schedule(season, week, season_type)
        except Exception:
            continue
        for game in games:
            if game["status"] == "final":
                snapshot = snapshots.get(game["game_id"])
                if snapshot and snapshot.get("seasonType", "regular") != season_type:
                    snapshot = None
                actual = None if game["home_score"] == game["away_score"] else game["home_team"] if game["home_score"] > game["away_score"] else game["away_team"]
                result = None if not snapshot else "push" if actual is None else "hit" if snapshot["winner"] == actual else "miss"
                eligibility = (snapshot or {}).get("profileEligibility") or {}
                completed.append({**game, "actualWinner": actual, "prediction": snapshot,
                                  "predictionResult": result,
                                  "betGrades": {profile: ("UNGRADED" if not snapshot else "NO_BET" if not eligibility.get(profile)
                                                                 else "PUSH" if result == "push" else "WIN" if result == "hit" else "LOSS")
                                                for profile in PROFILE_POLICY}})
    return sorted(completed, key=lambda game: game["kickoff_time"], reverse=True)


def prediction_performance(season: int, week: int, user_id: int, season_type: str = "regular") -> dict:
    rows = [row for row in historical_games(season, user_id, season_type) if row["week"] == week and row.get("prediction")]
    wins = sum(row["predictionResult"] == "hit" for row in rows)
    losses = sum(row["predictionResult"] == "miss" for row in rows)
    pushes = sum(row["predictionResult"] == "push" for row in rows)
    graded = wins + losses
    by_risk = {}
    for risk in ("SAFE", "BALANCED", "AGGRESSIVE"):
        subset = [row for row in rows if row["prediction"].get("riskLevel") == risk]
        risk_wins = sum(row["predictionResult"] == "hit" for row in subset)
        risk_losses = sum(row["predictionResult"] == "miss" for row in subset)
        by_risk[risk] = {"predictions": len(subset), "wins": risk_wins, "losses": risk_losses,
                         "pushes": len(subset) - risk_wins - risk_losses,
                         "accuracy": round(risk_wins / (risk_wins + risk_losses) * 100, 1) if risk_wins + risk_losses else None}
    profit, priced = 0.0, 0
    for row in rows:
        prediction, result = row["prediction"], row["predictionResult"]
        market, winner = prediction.get("market") or {}, prediction.get("winner")
        price = market.get("homeOdds") if winner == row["home_team"] else market.get("awayOdds")
        if price in (None, 0) or result not in {"hit", "miss", "push"}:
            continue
        priced += 1
        if result == "hit":
            profit += float(price) / 100 if float(price) > 0 else 100 / abs(float(price))
        elif result == "miss":
            profit -= 1.0
    calibration = []
    for lower in (.50, .55, .60, .65, .70, .75, .80, .85, .90, .95):
        upper = lower + .05
        bucket = [row for row in rows if lower <= float(row["prediction"].get("winProbability") or 0) < upper
                  and row["predictionResult"] in {"hit", "miss"}]
        if bucket:
            calibration.append({"range": f"{int(lower*100)}-{int(upper*100)-1}%", "count": len(bucket),
                                "averagePredictedProbability": round(sum(float(row["prediction"]["winProbability"]) for row in bucket) / len(bucket), 4),
                                "actualWinRate": round(sum(row["predictionResult"] == "hit" for row in bucket) / len(bucket), 4)})
    calibration_status = "AVAILABLE" if len(rows) >= 30 else "INSUFFICIENT_DATA"
    def segment(values: list[dict]) -> dict:
        segment_wins = sum(row["predictionResult"] == "hit" for row in values)
        segment_losses = sum(row["predictionResult"] == "miss" for row in values)
        return {"predictions": len(values), "wins": segment_wins, "losses": segment_losses,
                "pushes": sum(row["predictionResult"] == "push" for row in values),
                "accuracy": round(segment_wins / (segment_wins + segment_losses) * 100, 1)
                if segment_wins + segment_losses else None}
    favorites, underdogs, positive_edge, nonpositive_edge = [], [], [], []
    for row in rows:
        prediction = row["prediction"]
        market, winner = prediction.get("market") or {}, prediction.get("winner")
        price = market.get("homeOdds") if winner == row["home_team"] else market.get("awayOdds")
        if price is not None:
            (favorites if float(price) < 0 else underdogs).append(row)
        edge = prediction.get("edge")
        if edge is not None:
            (positive_edge if float(edge) > 0 else nonpositive_edge).append(row)
    return {"season": season, "week": week, "seasonType": _season_type(season_type),
            "predictions": len(rows), "wins": wins, "losses": losses, "pushes": pushes,
            "accuracy": round(wins / graded * 100, 1) if graded else None,
            "units": round(profit, 3) if priced else None,
            "roi": round(profit / priced * 100, 1) if priced else None,
            "roiCoverage": {"pricedPredictions": priced, "totalPredictions": len(rows)},
            "roiUnavailableReason": None if priced else "Executable pregame odds were not stored for these predictions." if rows else "No stored pregame predictions exist for this slate.",
            "byRiskLevel": by_risk,
            "byBetType": {"moneyline": {"predictions": len(rows), "wins": wins, "losses": losses, "pushes": pushes}},
            "byMarketPosition": {"favorites": segment(favorites), "underdogs": segment(underdogs)},
            "byEdgeSign": {"positive": segment(positive_edge), "zeroOrNegative": segment(nonpositive_edge)},
            "unsupportedBetTypes": {
                "spread": "No frozen spread prediction model is active.",
                "total": "No frozen total prediction model is active.",
            },
            "calibrationStatus": calibration_status, "calibrationBuckets": calibration,
            "calibrationNote": "At least 30 graded pregame predictions are required before treating calibration results as decision-ready."}


def current_week_context(season: int) -> dict:
    now = datetime.now(timezone.utc)
    candidates = []
    for season_type, weeks in (("preseason", range(0, 6)), ("regular", range(1, 19)),
                               ("postseason", range(1, 6))):
        for week in weeks:
            try:
                games = _schedule(season, week, season_type)
            except Exception:
                continue
            if not games:
                continue
            starts = [datetime.fromisoformat(game["kickoff_time"].replace("Z", "+00:00")) for game in games if game.get("kickoff_time")]
            if starts:
                candidates.append({"season": season, "seasonType": season_type, "week": week,
                                   "displayWeek": week, "providerWeek": games[0]["provider_week"],
                                   "weekKey": games[0]["week_key"], "weekLabel": games[0]["week_label"],
                                   "start": min(starts), "end": max(starts),
                                   "hasUpcoming": any(game["status"] == "scheduled" for game in games)})
    upcoming = [row for row in candidates if row["hasUpcoming"] and row["end"] >= now]
    if upcoming:
        chosen = min(upcoming, key=lambda row: row["start"])
    else:
        past = [row for row in candidates if row["start"] <= now]
        chosen = max(past, key=lambda row: row["end"], default=None)
    if not chosen:
        return {"season": season, "seasonType": "regular", "week": 1, "hasUpcoming": False}
    return {key: value for key, value in chosen.items() if key not in {"start", "end"}}


MULTI_GAME_POLICY = {
    "SAFE": {"min_legs": 2, "max_legs": 3, "minimum_probability": .60, "minimum_edge": .01},
    "BALANCED": {"min_legs": 3, "max_legs": 5, "minimum_probability": .54, "minimum_edge": 0.0},
    "AGGRESSIVE": {"min_legs": 4, "max_legs": 8, "minimum_probability": .50, "minimum_edge": .03},
}


def build_multi_game_parlay(*, season: int, week: int, season_type: str, profile: str,
                            selections: list[dict], user_id: int) -> tuple[ParlayResult, list[dict]]:
    """Validate user selections against one live weekly board; never invent prices."""
    profile = str(profile or "BALANCED").upper()
    if profile not in MULTI_GAME_POLICY:
        raise ValueError("profile must be SAFE, BALANCED, or AGGRESSIVE")
    board = weekly_board(season, week, profile, user_id, season_type=season_type)
    by_id = {game["game_id"]: game for game in board["items"]}
    policy = MULTI_GAME_POLICY[profile]
    accepted, rejected, seen_games = [], [], set()
    for selection in selections:
        game_id, team = str(selection.get("gameId") or ""), _abbr(selection.get("team"))
        game = by_id.get(game_id)
        reason = None
        if not game:
            reason = "game_not_in_selected_week"
        elif game_id in seen_games:
            reason = "duplicate_game"
        elif game["status"] != "scheduled":
            reason = "game_not_upcoming"
        elif game.get("predictionStatus") not in {"available", "pregame_snapshot"}:
            reason = "model_prediction_unavailable"
        elif team != game.get("winner"):
            reason = "selection_conflicts_with_model_winner"
        else:
            is_home = team == game["home_team"]
            odds = game["market"]["homeOdds"] if is_home else game["market"]["awayOdds"]
            implied = game["market"]["homeImpliedProbability"] if is_home else game["market"]["awayImpliedProbability"]
            probability = float(game["winProbability"])
            edge = None if implied is None else probability - float(implied)
            market_timestamp = game["market"].get("marketTimestamp")
            market_time = (datetime.fromisoformat(market_timestamp.replace("Z", "+00:00"))
                           if market_timestamp else None)
            if (odds is None or implied is None or game["market"].get("provider") != "the-odds-api"
                    or not game["market"].get("sportsbook") or market_time is None):
                reason = "verified_price_unavailable"
            elif datetime.now(timezone.utc) - market_time.astimezone(timezone.utc) > timedelta(minutes=45):
                reason = "stale_price"
            elif not game.get("recommendedBet"):
                reason = "model_decision_is_pass"
            elif probability < policy["minimum_probability"]:
                reason = "probability_below_profile_minimum"
            elif edge < policy["minimum_edge"]:
                reason = "edge_below_profile_minimum"
        if reason:
            rejected.append({"gameId": game_id, "team": team or None, "reason": reason})
            continue
        seen_games.add(game_id)
        accepted.append((game, team, int(odds), probability, edge))

    accepted.sort(key=lambda row: (row[4], row[3]), reverse=True)
    accepted = accepted[:policy["max_legs"]]
    legs = [ParlayLeg(
        sport=SportType.NFL, team=team, stat_type="MONEYLINE", odds=odds,
        prediction=f"{team} moneyline", confidence=round(probability * 100, 1),
        notes=(f"Model win probability {probability * 100:.1f}%; no-vig market edge {edge * 100:+.1f} points. "
               f"{game['away_team']} at {game['home_team']} ({game['market']['sportsbook']})."),
    ) for game, team, odds, probability, edge in accepted]
    if len(legs) < policy["min_legs"]:
        rejected.extend({"gameId": game["game_id"], "team": team, "reason": "insufficient_eligible_legs_for_profile"}
                        for game, team, *_ in accepted)
        legs = []
    combined_probability = 1.0
    decimal_odds = 1.0
    for leg in legs:
        combined_probability *= leg.confidence / 100
        decimal_odds *= 1 + (leg.odds / 100 if leg.odds > 0 else 100 / abs(leg.odds))
    estimated_odds = None
    if legs:
        profit_multiple = decimal_odds - 1
        estimated_odds = round(profit_multiple * 100 if decimal_odds >= 2 else -100 / profit_multiple)
    notes = (
        f"{profile.title()} multi-game parlay built from distinct scheduled games and verified moneyline prices."
        if legs else
        f"No {profile.lower()} parlay was built because fewer than {policy['min_legs']} selected games met the probability, edge, and price rules."
    )
    result = ParlayResult(
        parlay=Parlay(sport=SportType.NFL, difficulty=DifficultyLevel.from_input(profile), legs=legs, notes=notes),
        estimated_odds=estimated_odds, combined_probability=combined_probability if legs else 0, notes=notes,
    )
    return result, rejected


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
