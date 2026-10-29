"""Batched NBA schedule ingestion and provider normalization."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import date, datetime, timezone
import time

import requests

from .season import normalize_nba_season

ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
ESPN_REQUEST_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (compatible; SmartBetSports/2.0; +https://smartbetsports-api.vercel.app)",
}
logger = logging.getLogger(__name__)


class ScheduleIngestionError(RuntimeError):
    """Raised when any requested provider batch cannot be verified."""


def _iso(value: str) -> str:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _competitor(competition: dict, side: str) -> dict:
    return next((row for row in competition.get("competitors", []) if row.get("homeAway") == side), {})


def normalize_espn_event(event: dict, season: str | int) -> dict:
    competition = (event.get("competitions") or [{}])[0]
    home, away = _competitor(competition, "home"), _competitor(competition, "away")
    status = str((event.get("status") or {}).get("type", {}).get("state") or "pre").lower()
    detail = str((event.get("status") or {}).get("type", {}).get("detail") or "").lower()
    if "postpon" in detail:
        status = "POSTPONED"
    elif "cancel" in detail:
        status = "CANCELED"
    elif status == "post" or "final" in detail:
        status = "FINAL"
    elif status == "in":
        status = "LIVE"
    else:
        status = "SCHEDULED"
    season_id = normalize_nba_season(season)
    provider_season = event.get("season") or {}
    phase_value = provider_season.get("type") or provider_season.get("slug")
    if isinstance(phase_value, int) or str(phase_value or "").isdigit():
        season_phase = {1: "preseason", 2: "regular_season", 3: "postseason"}.get(
            int(phase_value), "unknown"
        )
    else:
        phase_text = str(phase_value or "").casefold()
        season_phase = (
            "preseason" if "pre" in phase_text else
            "postseason" if "post" in phase_text else
            "regular_season" if "regular" in phase_text else "unknown"
        )
    score_available = status in {"LIVE", "FINAL"}
    return {
        "game_id": f"espn-{event['id']}", "provider_event_id": str(event["id"]),
        "league": "nba", "season": season_id.code,
        "season_phase": season_phase,
        "kickoff_time": _iso(event["date"]), "status": status,
        "home_team": (home.get("team") or {}).get("abbreviation"),
        "away_team": (away.get("team") or {}).get("abbreviation"),
        "home_score": int(home["score"]) if score_available and str(home.get("score", "")).isdigit() else None,
        "away_score": int(away["score"]) if score_available and str(away.get("score", "")).isdigit() else None,
        "provider": "espn", "provider_timestamp": _iso(event["date"]),
    }


def month_batches(start: date, end: date) -> Iterable[str]:
    """Yield ESPN-supported calendar-month keys covering the requested range."""
    if end < start:
        raise ValueError("end date must not precede start date")
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield f"{year:04d}{month:02d}"
        month = month + 1
        if month == 13:
            year, month = year + 1, 1


def fetch_schedule(
    *, season: str | int, start: date, end: date,
    timeout: float = 10.0, http_get: Callable = requests.get, max_attempts: int = 3,
    sleeper: Callable[[float], None] = time.sleep,
) -> list[dict]:
    """Fetch a date range in bounded batches instead of one request per team/day."""
    normalized = normalize_nba_season(season)
    games: dict[str, dict] = {}
    for month in month_batches(start, end):
        response = None
        last_error = None
        succeeded = False
        for attempt in range(1, max(1, max_attempts) + 1):
            try:
                response = http_get(
                    ESPN_SCOREBOARD,
                    params={"dates": month, "limit": 1000},
                    headers=ESPN_REQUEST_HEADERS,
                    timeout=timeout,
                )
                response.raise_for_status()
                succeeded = True
                break
            except requests.RequestException as exc:
                last_error = exc
                logger.warning("nba_schedule_batch_failed month=%s attempt=%s error=%s",
                               month, attempt, type(exc).__name__)
                if attempt < max(1, max_attempts):
                    sleeper(float(2 ** (attempt - 1)))
        if not succeeded or response is None:
            raise ScheduleIngestionError(f"NBA schedule batch {month} could not be verified") from last_error
        for event in response.json().get("events", []):
            game = normalize_espn_event(event, normalized)
            game_date = _dt_date(game["kickoff_time"])
            if start <= game_date <= end:
                games[game["game_id"]] = game
    return sorted(games.values(), key=lambda row: (row["kickoff_time"], row["game_id"]))


def _dt_date(value: str) -> date:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
