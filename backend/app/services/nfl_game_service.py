from __future__ import annotations

import json
import logging
import os
import re
from copy import deepcopy
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from backend.app.services.game_status_service import (
    FINAL_STATUSES,
    lifecycle_cache_ttl,
    monotonic_status,
    normalize_game_status,
    parse_provider_timestamp,
)
from backend.app.services.schedule_service import _score, _team
from backend.app.services.nfl_context_service import build_team_context

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parents[3]
SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary"
GAME_ID_PATTERN = re.compile(r"^(?:espn-)?([0-9]{6,18})$")
MARKET_GROUPS = {
    "passing": ("passing",),
    "rushing": ("rushing",),
    "receiving": ("receiving", "receptions"),
    "receptions": ("reception",),
}

_CACHE: dict[str, dict[str, Any]] = {}
_REFRESH_LOCKS: dict[str, threading.Lock] = {}
_LAST_MANUAL_REFRESH: dict[str, float] = {}
_REGISTRY_LOCK = threading.Lock()


class GameNotFoundError(LookupError):
    pass


class InvalidGameIdError(ValueError):
    pass


def canonical_game_id(value: str) -> str:
    match = GAME_ID_PATTERN.fullmatch(str(value or "").strip().lower())
    if not match:
        raise InvalidGameIdError("Use a valid canonical ESPN NFL game ID.")
    return match.group(1)


def _iso(value: Any = None) -> str:
    return parse_provider_timestamp(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _fetch_espn_summary(game_id: str) -> dict[str, Any]:
    response = requests.get(SUMMARY_URL, params={"event": game_id}, timeout=8)
    if response.status_code == 404:
        raise GameNotFoundError(game_id)
    response.raise_for_status()
    payload = response.json()
    if not payload.get("header"):
        raise GameNotFoundError(game_id)
    return payload


def _phase(season_type: Any) -> str:
    if isinstance(season_type, dict):
        name = season_type.get("slug") or season_type.get("name") or season_type.get("type")
    else:
        name = season_type
    if isinstance(name, int):
        return {1: "preseason", 2: "regular_season", 3: "postseason"}.get(name, "unknown")
    text = str(name or "unknown").lower().replace(" ", "_").replace("-", "_")
    if text.isdigit():
        return {"1": "preseason", "2": "regular_season", "3": "postseason"}.get(text, "unknown")
    if "pre" in text:
        return "preseason"
    if "post" in text:
        return "postseason"
    if "regular" in text:
        return "regular_season"
    return text


def _game_from_summary(game_id: str, payload: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    header = payload.get("header") or {}
    competition = (header.get("competitions") or [{}])[0]
    competitors = competition.get("competitors") or []
    if len(competitors) < 2:
        raise GameNotFoundError(game_id)
    home = next((row for row in competitors if row.get("homeAway") == "home"), competitors[0])
    away = next((row for row in competitors if row.get("homeAway") == "away"), competitors[-1])
    status_obj = competition.get("status") or header.get("status") or {}
    status_type = status_obj.get("type") or {}
    provider_updated = status_obj.get("lastUpdated") or status_type.get("lastUpdated") or payload.get("lastUpdated")
    incoming = normalize_game_status(
        status_type.get("name") or status_type.get("state"),
        status_type.get("detail") or status_type.get("shortDetail"),
        bool(status_type.get("completed")),
    )
    status = monotonic_status(
        (previous or {}).get("status"), incoming,
        (previous or {}).get("statusUpdatedAt"), provider_updated,
    )
    kickoff = competition.get("date") or header.get("date")
    kickoff_dt = parse_provider_timestamp(kickoff)
    season = header.get("season") or competition.get("season") or {}
    season_year = season.get("year") or kickoff_dt.year
    phase = _phase(season.get("type") or competition.get("type"))
    week_obj = header.get("week") or competition.get("week") or {}
    week = week_obj.get("number") if isinstance(week_obj, dict) else week_obj
    venue = competition.get("venue") or (payload.get("gameInfo") or {}).get("venue") or {}
    address = venue.get("address") or {}
    broadcasts = []
    for broadcast in competition.get("broadcasts") or []:
        names = broadcast.get("names") or [broadcast.get("name")]
        broadcasts.extend(name for name in names if name)
    return {
        "id": game_id,
        "canonicalId": game_id,
        "league": "nfl",
        "season": int(season_year),
        "seasonPhase": phase,
        "week": int(week) if week is not None else None,
        "phaseWeekKey": f"{season_year}:{phase}:w{week or 0}",
        "startTimeUtc": _iso(kickoff_dt),
        "status": status,
        "statusDetail": status_type.get("detail") or status_type.get("shortDetail"),
        "statusUpdatedAt": _iso(provider_updated) if provider_updated else None,
        "awayTeam": _team(away, "nfl").model_dump(mode="json"),
        "homeTeam": _team(home, "nfl").model_dump(mode="json"),
        "awayScore": _score(away),
        "homeScore": _score(home),
        "venue": venue.get("fullName"),
        "city": ", ".join(filter(None, (address.get("city"), address.get("state")))) or None,
        "broadcast": sorted(set(broadcasts)),
    }


def _stat_value(value: Any) -> int | float | str | None:
    if value in (None, ""):
        return None
    text = str(value).replace(",", "")
    try:
        number = float(text)
        return int(number) if number.is_integer() else number
    except ValueError:
        return str(value)


def _actuals(payload: dict[str, Any], game: dict[str, Any]) -> dict[str, Any]:
    boxscore = payload.get("boxscore") or {}
    team_stats = []
    for row in boxscore.get("teams") or []:
        team = row.get("team") or {}
        statistics = [
            {"name": stat.get("label") or stat.get("name"), "value": _stat_value(stat.get("displayValue") or stat.get("value"))}
            for stat in row.get("statistics") or []
            if stat.get("label") or stat.get("name")
        ]
        if not statistics:
            continue
        team_stats.append({
            "team": team.get("abbreviation"),
            "statistics": statistics,
        })
    groups: dict[str, list[dict[str, Any]]] = {key: [] for key in MARKET_GROUPS}
    for team_row in boxscore.get("players") or []:
        team = (team_row.get("team") or {}).get("abbreviation")
        for stat_group in team_row.get("statistics") or []:
            raw_name = str(stat_group.get("name") or "").lower()
            group = next((name for name, needles in MARKET_GROUPS.items() if any(needle in raw_name for needle in needles)), None)
            if not group:
                continue
            labels = stat_group.get("labels") or []
            for athlete_row in stat_group.get("athletes") or []:
                athlete = athlete_row.get("athlete") or {}
                values = athlete_row.get("stats") or []
                groups[group].append({
                    "playerId": str(athlete.get("id") or ""),
                    "playerName": athlete.get("displayName") or athlete.get("shortName"),
                    "position": (athlete.get("position") or {}).get("abbreviation"),
                    "team": team,
                    "stats": {str(label): _stat_value(values[index] if index < len(values) else None) for index, label in enumerate(labels)},
                })
    has_stats = bool(team_stats or any(groups.values()))
    available = game.get("status") in ({"live", "halftime"} | FINAL_STATUSES) and has_stats
    score_available = game.get("awayScore") is not None and game.get("homeScore") is not None
    return {"available": available, "providerDataAvailable": has_stats, "scoreAvailable": score_available, "teamStats": team_stats, "playerGroups": [{"group": key, "items": value} for key, value in groups.items()]}


def _prediction_snapshot(game_id: str, season: int, week: int | None, phase: str) -> dict[str, Any]:
    if phase == "preseason":
        return {"available": False, "frozen": True, "reason": "No frozen System A preseason prediction artifact is available for this game.", "groups": []}
    if week is None:
        return {"available": False, "frozen": True, "reason": "Game week is unavailable; no prediction artifact was selected.", "groups": []}
    path = BASE_DIR / "backtesting" / "data" / "snapshots" / "nfl" / str(season) / f"week_{week:02d}" / "player_prop_predictions.json"
    if not path.exists():
        return {"available": False, "frozen": True, "reason": "No frozen System A prediction artifact is available for this game.", "groups": []}
    rows = json.loads(path.read_text(encoding="utf-8"))
    candidates = [row for row in rows if str(row.get("game_id", "")).removeprefix("espn-") == game_id]
    if not candidates:
        return {"available": False, "frozen": True, "reason": "The frozen weekly artifact contains no predictions for this game.", "groups": []}
    collapsed: dict[tuple[str, str], dict[str, Any]] = {}
    for row in candidates:
        key = (str(row.get("canonical_player_id") or row.get("player_name")), str(row.get("market")))
        item = collapsed.setdefault(key, {
            "playerId": row.get("canonical_player_id"),
            "playerName": row.get("player_name"),
            "team": row.get("team"),
            "market": row.get("market"),
            "line": row.get("line"),
            "mean": (row.get("distribution_summary") or {}).get("mean"),
            "variance": ((row.get("distribution_summary") or {}).get("standard_deviation") or 0) ** 2,
            "quantiles": (row.get("distribution_summary") or {}).get("quantiles") or {},
            "historyDepth": (row.get("provenance") or {}).get("player_history_games"),
            "stability": {"available": False, "reason": "Not published in this frozen serving artifact."},
            "unresolvedFlags": row.get("unresolved_flags") or row.get("flags") or [],
            "probabilities": {},
        })
        if row.get("side"):
            item["probabilities"][str(row["side"]).lower()] = row.get("model_probability")
    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key in MARKET_GROUPS}
    for item in collapsed.values():
        market = str(item.get("market") or "").lower()
        group = next((name for name, needles in MARKET_GROUPS.items() if any(needle in market for needle in needles)), "receiving")
        grouped[group].append(item)
    first = candidates[0]
    return {
        "available": True,
        "frozen": True,
        "modelVersion": first.get("model_version"),
        "predictionCutoff": first.get("prediction_cutoff"),
        "generatedAt": first.get("generated_at"),
        "researchPolicyId": "nfl_system_a_forward_shadow_v1",
        "groups": [{"group": name, "items": sorted(items, key=lambda item: (item.get("team") or "", item.get("playerName") or ""))} for name, items in grouped.items()],
    }


def _comparison(predictions: dict[str, Any], actuals: dict[str, Any], status: str) -> dict[str, Any]:
    if status not in FINAL_STATUSES:
        return {"available": False, "reason": "Prediction-versus-actual comparison is available after the game is final.", "items": []}
    if not predictions.get("available") or not actuals.get("available"):
        return {"available": False, "reason": "Both frozen predictions and final box-score data are required.", "items": []}
    actual_index: dict[tuple[str, str], Any] = {}
    stat_aliases = {"passing_yards": "YDS", "passing_tds": "TD", "rushing_yards": "YDS", "receiving_yards": "YDS", "receptions": "REC"}
    for group in actuals.get("playerGroups") or []:
        for item in group.get("items") or []:
            for label, value in (item.get("stats") or {}).items():
                actual_index[(str(item.get("playerId")), f"{group['group']}:{label.upper()}")] = value
    items = []
    for group in predictions.get("groups") or []:
        for prediction in group.get("items") or []:
            label = stat_aliases.get(str(prediction.get("market")))
            actual = actual_index.get((str(prediction.get("playerId")), f"{group['group']}:{label}")) if label else None
            if isinstance(actual, (int, float)):
                mean = prediction.get("mean")
                items.append({**prediction, "actual": actual, "error": round(actual - mean, 2) if isinstance(mean, (int, float)) else None})
    return {"available": bool(items), "reason": None if items else "No comparable player-stat rows were found.", "items": items}


def _stale_audit(game: dict[str, Any], previous: dict[str, Any] | None, checked_at: str, actuals: dict[str, Any]) -> dict[str, Any]:
    reasons = []
    kickoff = parse_provider_timestamp(game.get("startTimeUtc"))
    now_dt = parse_provider_timestamp(checked_at)
    if game.get("status") in {"scheduled", "pregame", "unknown"} and now_dt > kickoff + timedelta(hours=6):
        reasons.append("STALE_SCHEDULE_STATUS")
    if game.get("status") in {"live", "halftime"} and now_dt > kickoff + timedelta(hours=8):
        reasons.append("LIVE_STATUS_STALE")
    scores_available = game.get("awayScore") is not None and game.get("homeScore") is not None
    if game.get("status") not in FINAL_STATUSES and scores_available and now_dt > kickoff + timedelta(hours=4):
        reasons.append("FINAL_SCORE_WITH_NONFINAL_STATUS")
    if game.get("status") not in FINAL_STATUSES and actuals.get("providerDataAvailable") and now_dt > kickoff + timedelta(hours=4):
        reasons.append("STATS_WITH_NONFINAL_STATUS")
    if game.get("status") in FINAL_STATUSES and not actuals.get("available"):
        reasons.append("MISSING_FINAL_BOX_SCORE")
    if previous and previous.get("status") not in FINAL_STATUSES and game.get("status") in FINAL_STATUSES:
        reasons.append("PROVIDER_CONFIRMED_FINAL_REPAIR")
    if previous and previous.get("status") in FINAL_STATUSES and game.get("status") in FINAL_STATUSES:
        reasons.append("FINAL_MONOTONICITY_PRESERVED")
    repaired = "PROVIDER_CONFIRMED_FINAL_REPAIR" in reasons
    return {"checkedAt": checked_at, "stale": bool(reasons), "reasonCodes": reasons, "repaired": repaired, "repairAction": "ADVANCE_TO_FINAL" if repaired else None}


def _assemble(game_id: str, payload: dict[str, Any], previous_response: dict[str, Any] | None, trigger: str) -> dict[str, Any]:
    checked_at = _iso()
    previous_game = (previous_response or {}).get("game")
    game = _game_from_summary(game_id, payload, previous_game)
    actuals = _actuals(payload, game)
    predictions = _prediction_snapshot(game_id, game["season"], game.get("week"), game["seasonPhase"])
    fixture = payload.get("_smartbetFixture") or {}
    team_context = fixture.get("teamContext") or build_team_context(game)
    if fixture.get("predictions"):
        predictions = fixture["predictions"]
    audit = _stale_audit(game, previous_game, checked_at, actuals)
    deterministic_final = {"FINAL_SCORE_WITH_NONFINAL_STATUS", "STATS_WITH_NONFINAL_STATUS"}.issubset(audit["reasonCodes"])
    if deterministic_final:
        game["status"] = "final"
        actuals["available"] = actuals.get("providerDataAvailable", False)
        audit.update({"repaired": True, "repairAction": "ADVANCE_TO_FINAL_FROM_SCORE_AND_BOX_SCORE"})
    source_updated = game.get("statusUpdatedAt")
    source_age = max(0, int((parse_provider_timestamp(checked_at) - parse_provider_timestamp(source_updated)).total_seconds())) if source_updated else None
    ttl = lifecycle_cache_ttl(game["status"], game["startTimeUtc"], checked_at, stats_complete=actuals.get("available", False))
    freshness_state = "source_unavailable" if source_age is None else "fresh" if source_age <= ttl * 2 else "stale"
    response = {
        "game": game,
        "lifecycle": {
            "status": game["status"],
            "providerStatus": game.get("statusDetail"),
            "lastProviderUpdate": game.get("statusUpdatedAt"),
            "fetchedAt": checked_at,
            "freshnessState": freshness_state,
            "sourceAgeSeconds": source_age,
            "localCacheAgeSeconds": 0,
            "cacheTtlSeconds": ttl,
        },
        "teamContext": team_context,
        "predictions": predictions,
        "actuals": actuals,
        "comparison": {},
        "sources": {
            "scheduleStatusScore": "ESPN scoreboard/summary (free existing provider)",
            "boxScore": "ESPN summary (free existing provider)",
            "predictions": "Versioned frozen System A snapshot artifact",
            "paidProviderContacted": False,
        },
        "dataAvailability": {"contextAvailable": team_context.get("available", False), "predictionsAvailable": predictions.get("available", False), "statsAvailable": actuals.get("available", False)},
        "refresh": {"trigger": trigger, "coalesced": False, "completedAt": checked_at},
        "reconciliation": audit,
    }
    response["comparison"] = _comparison(predictions, actuals, game["status"])
    logger.info("nfl_game_refresh_end %s", json.dumps({"gameId": game_id, "priorStatus": (previous_game or {}).get("status"), "providerStatus": game.get("statusDetail"), "resultStatus": game["status"], "sourceTimestamp": game.get("statusUpdatedAt"), "scoreAvailable": game.get("awayScore") is not None and game.get("homeScore") is not None, "statsAvailable": actuals.get("available"), "predictionsAvailable": predictions.get("available"), "trigger": trigger, **audit}, sort_keys=True))
    return response


def _lock_for(game_id: str) -> threading.Lock:
    with _REGISTRY_LOCK:
        return _REFRESH_LOCKS.setdefault(game_id, threading.Lock())


def _fetch_and_store(canonical: str, current: dict[str, Any] | None, trigger: str, fetcher: Callable[[str], dict[str, Any]] | None) -> dict[str, Any]:
    logger.info("nfl_game_refresh_start %s", json.dumps({"gameId": canonical, "priorStatus": ((current or {}).get("response") or {}).get("game", {}).get("status"), "trigger": trigger}, sort_keys=True))
    try:
        payload = (fetcher or _fetch_espn_summary)(canonical)
    except Exception:
        logger.exception("nfl_game_refresh_error", extra={"game_id": canonical, "trigger": trigger})
        raise
    response = _assemble(canonical, payload, current["response"] if current else None, trigger)
    stored = time.time()
    _CACHE[canonical] = {"response": deepcopy(response), "storedAt": stored, "expiresAt": stored + response["lifecycle"]["cacheTtlSeconds"]}
    return deepcopy(response)


def get_nfl_game_detail(game_id: str, *, force: bool = False, trigger: str = "read", fetcher: Callable[[str], dict[str, Any]] | None = None) -> dict[str, Any]:
    canonical = canonical_game_id(game_id)
    current = _CACHE.get(canonical)
    now_epoch = time.time()
    if current and not force and current["expiresAt"] > now_epoch:
        response = deepcopy(current["response"])
        response["lifecycle"]["localCacheAgeSeconds"] = max(0, int(now_epoch - current["storedAt"]))
        return response
    lock = _lock_for(canonical)
    with lock:
        current = _CACHE.get(canonical)
        if current and not force and current["expiresAt"] > time.time():
            return deepcopy(current["response"])
        return _fetch_and_store(canonical, current, trigger, fetcher)


def refresh_nfl_game_detail(game_id: str, *, fetcher: Callable[[str], dict[str, Any]] | None = None) -> dict[str, Any]:
    canonical = canonical_game_id(game_id)
    debounce_seconds = int(os.getenv("NFL_MANUAL_REFRESH_DEBOUNCE_SECONDS", "10"))
    lock = _lock_for(canonical)
    with lock:
        current = _CACHE.get(canonical)
        last = _LAST_MANUAL_REFRESH.get(canonical, 0)
        if time.time() - last < debounce_seconds and current:
            response = deepcopy(current["response"])
            response["refresh"] = {**response["refresh"], "trigger": "manual", "coalesced": True}
            return response
        _LAST_MANUAL_REFRESH[canonical] = time.time()
        return _fetch_and_store(canonical, current, "manual", fetcher)


def clear_nfl_game_cache() -> None:
    _CACHE.clear()
    _LAST_MANUAL_REFRESH.clear()
