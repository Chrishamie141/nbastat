"""Deterministic game matching helpers for provider event IDs and internal game IDs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

TEAM_ALIASES = {"ARI":"ARI","ARIZONA CARDINALS":"ARI","ATL":"ATL","ATLANTA FALCONS":"ATL","BAL":"BAL","BALTIMORE RAVENS":"BAL","BUF":"BUF","BUFFALO BILLS":"BUF","CAR":"CAR","CAROLINA PANTHERS":"CAR","CHI":"CHI","CHICAGO BEARS":"CHI","CIN":"CIN","CINCINNATI BENGALS":"CIN","CLE":"CLE","CLEVELAND BROWNS":"CLE","DAL":"DAL","DALLAS COWBOYS":"DAL","DEN":"DEN","DENVER BRONCOS":"DEN","DET":"DET","DETROIT LIONS":"DET","GB":"GB","GREEN BAY PACKERS":"GB","HOU":"HOU","HOUSTON TEXANS":"HOU","IND":"IND","INDIANAPOLIS COLTS":"IND","JAX":"JAX","JACKSONVILLE JAGUARS":"JAX","KC":"KC","KAN":"KC","KANSAS CITY CHIEFS":"KC","LAC":"LAC","LOS ANGELES CHARGERS":"LAC","LAR":"LAR","LOS ANGELES RAMS":"LAR","LV":"LV","LAS VEGAS RAIDERS":"LV","MIA":"MIA","MIAMI DOLPHINS":"MIA","MIN":"MIN","MINNESOTA VIKINGS":"MIN","NE":"NE","NEW ENGLAND PATRIOTS":"NE","NO":"NO","NEW ORLEANS SAINTS":"NO","NYG":"NYG","NEW YORK GIANTS":"NYG","NYJ":"NYJ","NEW YORK JETS":"NYJ","PHI":"PHI","PHILADELPHIA EAGLES":"PHI","PIT":"PIT","PITTSBURGH STEELERS":"PIT","SEA":"SEA","SEATTLE SEAHAWKS":"SEA","SF":"SF","SAN FRANCISCO 49ERS":"SF","TB":"TB","TAMPA BAY BUCCANEERS":"TB","TEN":"TEN","TENNESSEE TITANS":"TEN","WAS":"WAS","WSH":"WAS","WASHINGTON COMMANDERS":"WAS"}

@dataclass
class MatchDiagnostic:
    matched: bool
    game_id: str | None = None
    strategy: str | None = None
    reasons: list[str] = field(default_factory=list)


def normalize_team(value: Any) -> str:
    key = re.sub(r"[^A-Z0-9 ]", "", str(value or "").upper()).strip()
    return TEAM_ALIASES.get(key, key)


def parse_dt(value: Any):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def match_game(event: dict[str, Any], games: list[dict[str, Any]], *, tolerance_minutes: int = 180, league: str | None = None) -> MatchDiagnostic:
    provider_id = event.get("provider_game_id") or event.get("event_id") or event.get("id")
    if provider_id:
        for game in games:
            provider_ids = {game.get("provider_game_id"), game.get("event_id"), game.get("odds_event_id"), game.get("the_odds_api_event_id")}
            if provider_id in provider_ids:
                return MatchDiagnostic(True, game.get("game_id"), "provider_game_id")
    event_dt = parse_dt(event.get("commence_time") or event.get("kickoff_time"))
    event_home = normalize_team(event.get("home_team"))
    event_away = normalize_team(event.get("away_team"))
    candidates = []
    for game in games:
        reasons = []
        if league and str(game.get("league", league)).lower() != str(league).lower():
            reasons.append("league_mismatch")
        game_dt = parse_dt(game.get("kickoff_time") or game.get("commence_time"))
        if not event_dt or not game_dt:
            reasons.append("missing_kickoff_datetime")
        elif abs((event_dt - game_dt).total_seconds()) > tolerance_minutes * 60:
            reasons.append("kickoff_datetime_outside_tolerance")
        if normalize_team(game.get("home_team")) != event_home:
            reasons.append("home_team_mismatch")
        if normalize_team(game.get("away_team")) != event_away:
            reasons.append("away_team_mismatch")
        if not reasons:
            candidates.append(game)
    if len(candidates) == 1:
        return MatchDiagnostic(True, candidates[0].get("game_id"), "datetime_home_away_league")
    if len(candidates) > 1:
        return MatchDiagnostic(False, None, None, ["ambiguous_match"])
    return MatchDiagnostic(False, None, None, ["provider_game_id_not_found", "no_datetime_team_league_match"])
