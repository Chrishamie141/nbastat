"""Deterministic reconciliation of provider events with canonical games."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ESPN, The Odds API, and common data exports do not consistently use the same
# team names.  Keep the canonical value provider-independent and explicit.
_NFL_TEAMS = {
    "ARI": ("Arizona Cardinals", "Arizona", "Cardinals", "ARZ"),
    "ATL": ("Atlanta Falcons", "Atlanta", "Falcons"),
    "BAL": ("Baltimore Ravens", "Baltimore", "Ravens"),
    "BUF": ("Buffalo Bills", "Buffalo", "Bills"),
    "CAR": ("Carolina Panthers", "Carolina", "Panthers"),
    "CHI": ("Chicago Bears", "Chicago", "Bears"),
    "CIN": ("Cincinnati Bengals", "Cincinnati", "Bengals"),
    "CLE": ("Cleveland Browns", "Cleveland", "Browns"),
    "DAL": ("Dallas Cowboys", "Dallas", "Cowboys"),
    "DEN": ("Denver Broncos", "Denver", "Broncos"),
    "DET": ("Detroit Lions", "Detroit", "Lions"),
    "GB": ("Green Bay Packers", "Green Bay", "Packers", "GNB"),
    "HOU": ("Houston Texans", "Houston", "Texans"),
    "IND": ("Indianapolis Colts", "Indianapolis", "Colts"),
    "JAX": ("Jacksonville Jaguars", "Jacksonville", "Jaguars", "JAC"),
    "KC": ("Kansas City Chiefs", "Kansas City", "Chiefs", "KAN"),
    "LAC": ("Los Angeles Chargers", "LA Chargers", "L.A. Chargers", "Chargers", "SD", "SDG"),
    "LAR": ("Los Angeles Rams", "LA Rams", "L.A. Rams", "Rams", "STL"),
    "LV": ("Las Vegas Raiders", "Las Vegas", "Raiders", "OAK"),
    "MIA": ("Miami Dolphins", "Miami", "Dolphins"),
    "MIN": ("Minnesota Vikings", "Minnesota", "Vikings"),
    "NE": ("New England Patriots", "New England", "Patriots", "N.E.", "NWE"),
    "NO": ("New Orleans Saints", "New Orleans", "Saints", "N.O.", "NOR"),
    "NYG": ("New York Giants", "NY Giants", "N.Y. Giants", "Giants"),
    "NYJ": ("New York Jets", "NY Jets", "N.Y. Jets", "Jets"),
    "PHI": ("Philadelphia Eagles", "Philadelphia", "Eagles"),
    "PIT": ("Pittsburgh Steelers", "Pittsburgh", "Steelers"),
    "SEA": ("Seattle Seahawks", "Seattle", "Seahawks"),
    "SF": ("San Francisco 49ers", "San Francisco", "49ers", "S.F.", "SFO"),
    "TB": ("Tampa Bay Buccaneers", "Tampa Bay", "Buccaneers", "Bucs", "T.B.", "TAM"),
    "TEN": ("Tennessee Titans", "Tennessee", "Titans"),
    "WAS": ("Washington Commanders", "Washington Football Team", "Washington", "Commanders", "WAS", "WSH", "WFT"),
}


def _team_key(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


TEAM_ALIASES = {
    _team_key(alias): abbreviation
    for abbreviation, aliases in _NFL_TEAMS.items()
    for alias in (abbreviation, *aliases)
}


@dataclass
class MatchDiagnostic:
    matched: bool
    game_id: str | None = None
    strategy: str | None = None
    reasons: list[str] = field(default_factory=list)
    closest_game_id: str | None = None
    closest_home_team: str | None = None
    closest_away_team: str | None = None
    closest_kickoff_time: str | None = None


def normalize_team(value: Any) -> str:
    """Return a stable NFL abbreviation after punctuation/case normalization."""
    key = _team_key(value)
    return TEAM_ALIASES.get(key, key)


def parse_dt(value: Any) -> datetime | None:
    """Parse an ISO timestamp and always return an aware UTC datetime."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _event_league(event: dict[str, Any], fallback: str | None) -> str | None:
    value = event.get("league") or event.get("sport") or event.get("sport_key") or fallback
    if not value:
        return None
    value = str(value).lower().strip()
    return "nfl" if value in {"nfl", "americanfootball_nfl"} else value


def match_game(
    event: dict[str, Any],
    games: list[dict[str, Any]],
    *,
    tolerance_minutes: int = 180,
    league: str | None = None,
) -> MatchDiagnostic:
    """Match once at event level, returning an actionable failure diagnostic."""
    provider_id = event.get("provider_game_id") or event.get("event_id") or event.get("id")
    if provider_id:
        for game in games:
            provider_ids = {
                game.get("provider_game_id"), game.get("event_id"),
                game.get("odds_event_id"), game.get("the_odds_api_event_id"),
            }
            if str(provider_id) in {str(value) for value in provider_ids if value is not None}:
                return MatchDiagnostic(True, game.get("game_id"), "provider_game_id")

    event_dt = parse_dt(event.get("commence_time") or event.get("kickoff_time"))
    event_home = normalize_team(event.get("home_team"))
    event_away = normalize_team(event.get("away_team"))
    event_league = _event_league(event, league)
    scored: list[tuple[int, float, dict[str, Any], list[str]]] = []
    for game in games:
        reasons: list[str] = []
        game_league = _event_league(game, league)
        if event_league and game_league and event_league != game_league:
            reasons.append("league_mismatch")
        game_dt = parse_dt(game.get("kickoff_time") or game.get("commence_time"))
        delta = float("inf")
        if not event_dt or not game_dt:
            reasons.append("missing_kickoff_datetime")
        else:
            delta = abs((event_dt - game_dt).total_seconds())
            if delta > tolerance_minutes * 60:
                reasons.append("kickoff_datetime_outside_tolerance")
        if not event_home or normalize_team(game.get("home_team")) != event_home:
            reasons.append("home_team_mismatch")
        if not event_away or normalize_team(game.get("away_team")) != event_away:
            reasons.append("away_team_mismatch")
        scored.append((len(reasons), delta, game, reasons))

    matches = [item for item in scored if not item[3]]
    if len(matches) == 1:
        return MatchDiagnostic(True, matches[0][2].get("game_id"), "datetime_home_away_league")
    if len(matches) > 1:
        return MatchDiagnostic(False, reasons=["ambiguous_match"])
    if not scored:
        return MatchDiagnostic(False, reasons=["no_canonical_games"])

    _, _, closest, reasons = min(scored, key=lambda item: (item[0], item[1]))
    if provider_id:
        reasons = ["provider_game_id_not_found", *reasons]
    return MatchDiagnostic(
        False,
        reasons=reasons,
        closest_game_id=closest.get("game_id"),
        closest_home_team=closest.get("home_team"),
        closest_away_team=closest.get("away_team"),
        closest_kickoff_time=closest.get("kickoff_time") or closest.get("commence_time"),
    )
