"""Local JSON cache helpers for rosters and betting prediction rows."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from team_utils import normalize_team_abbreviation

CACHE_DIR = Path("data/cache")
ROSTER_CACHE_MAX_AGE_HOURS = 72
PREDICTION_CACHE_MAX_AGE_HOURS = 24


def _utc_now():
    return datetime.now(timezone.utc)


def _parse_cached_at(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _age_hours(cached_at):
    if cached_at is None:
        return None
    if cached_at.tzinfo is None:
        cached_at = cached_at.replace(tzinfo=timezone.utc)
    return (_utc_now() - cached_at).total_seconds() / 3600


def _is_stale(cached_at, max_age_hours):
    age = _age_hours(cached_at)
    return age is None or age > max_age_hours


def _write_json(path, payload):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as cache_file:
        json.dump(payload, cache_file, indent=2)


def save_roster_cache(team_abbr, roster):
    """Save a local roster snapshot for one team abbreviation."""
    team_abbr = normalize_team_abbreviation(team_abbr)
    payload = {
        "team_abbr": team_abbr,
        "cached_at": _utc_now().isoformat(),
        "roster": list(roster or []),
    }
    _write_json(CACHE_DIR / f"roster_{team_abbr}.json", payload)
    return payload


def load_roster_cache(team_abbr, max_age_hours=ROSTER_CACHE_MAX_AGE_HOURS, allow_stale=False):
    """Load a roster cache payload, returning None when missing or too old.

    Set allow_stale=True to receive stale cache data with ``stale`` set in the
    returned payload. The returned object includes ``team_abbr``, ``cached_at``,
    ``roster``, and ``stale``.
    """
    team_abbr = normalize_team_abbreviation(team_abbr)
    path = CACHE_DIR / f"roster_{team_abbr}.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as cache_file:
            payload = json.load(cache_file)
    except (OSError, json.JSONDecodeError):
        return None

    cached_at = _parse_cached_at(payload.get("cached_at"))
    stale = _is_stale(cached_at, max_age_hours)
    if stale and not allow_stale:
        return None
    payload["team_abbr"] = normalize_team_abbreviation(payload.get("team_abbr") or team_abbr)
    payload["roster"] = list(payload.get("roster") or [])
    payload["stale"] = stale
    payload["cached_at"] = cached_at.isoformat() if cached_at else payload.get("cached_at")
    return payload


def _prediction_cache_path(game_date, team, opponent):
    game_date = str(game_date or "unknown")
    team = normalize_team_abbreviation(team)
    opponent = normalize_team_abbreviation(opponent or "unknown")
    return CACHE_DIR / f"predictions_{game_date}*{team}*{opponent}.json"


def save_prediction_cache(game_date, team, opponent, prediction_rows):
    """Save structured betting prediction rows for one game/team pairing."""
    team = normalize_team_abbreviation(team)
    opponent = normalize_team_abbreviation(opponent or "unknown")
    payload = {
        "game_date": game_date,
        "team": team,
        "opponent": opponent,
        "cached_at": _utc_now().isoformat(),
        "prediction_rows": list(prediction_rows or []),
    }
    _write_json(_prediction_cache_path(game_date, team, opponent), payload)
    return payload


def load_prediction_cache(game_date, team, opponent, max_age_hours=PREDICTION_CACHE_MAX_AGE_HOURS):
    """Load fresh cached betting prediction rows for one game/team pairing."""
    path = _prediction_cache_path(game_date, team, opponent)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as cache_file:
            payload = json.load(cache_file)
    except (OSError, json.JSONDecodeError):
        return None

    cached_at = _parse_cached_at(payload.get("cached_at"))
    if _is_stale(cached_at, max_age_hours):
        return None
    payload["cached_at"] = cached_at.isoformat() if cached_at else payload.get("cached_at")
    payload["team"] = normalize_team_abbreviation(payload.get("team") or team)
    payload["opponent"] = normalize_team_abbreviation(payload.get("opponent") or opponent or "unknown")
    payload["prediction_rows"] = list(payload.get("prediction_rows") or [])
    return payload
