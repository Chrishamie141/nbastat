"""Local JSON cache helpers for rosters and betting prediction rows."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from team_utils import normalize_team_abbreviation

CACHE_DIR = Path("data/cache")
ROSTER_CACHE_MAX_AGE_HOURS = 72
PREDICTION_CACHE_MAX_AGE_HOURS = 24
INVALID_WINDOWS_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]+')


def safe_cache_key(value: str) -> str:
    """Return a Windows-safe cache filename component."""
    raw_value = "" if value is None else str(value).strip()
    if not raw_value:
        return "unknown"

    safe_value = INVALID_WINDOWS_FILENAME_CHARS.sub("_", raw_value)
    safe_value = re.sub(r"\s+", "_", safe_value)
    safe_value = re.sub(r"_+", "_", safe_value).strip("_")
    if not safe_value:
        return "unknown"

    if "_" not in safe_value and safe_value.isalpha() and 2 <= len(safe_value) <= 4:
        return normalize_team_abbreviation(safe_value)
    return safe_value


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
    _write_json(CACHE_DIR / f"roster_{safe_cache_key(team_abbr)}.json", payload)
    return payload


def load_roster_cache(team_abbr, max_age_hours=ROSTER_CACHE_MAX_AGE_HOURS, allow_stale=False):
    """Load a roster cache payload, returning None when missing or too old.

    Set allow_stale=True to receive stale cache data with ``stale`` set in the
    returned payload. The returned object includes ``team_abbr``, ``cached_at``,
    ``roster``, and ``stale``.
    """
    team_abbr = normalize_team_abbreviation(team_abbr)
    path = CACHE_DIR / f"roster_{safe_cache_key(team_abbr)}.json"
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
    game_date = safe_cache_key(game_date)
    team = safe_cache_key(team)
    opponent = safe_cache_key(opponent)
    return CACHE_DIR / f"predictions_{game_date}_{team}_{opponent}.json"


def save_prediction_cache(game_date, team, opponent, prediction_rows):
    """Save structured betting prediction rows for one game/team pairing."""
    game_date = safe_cache_key(game_date)
    team = safe_cache_key(team)
    opponent = safe_cache_key(opponent)
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
    payload["game_date"] = safe_cache_key(payload.get("game_date") or game_date)
    payload["team"] = safe_cache_key(payload.get("team") or team)
    payload["opponent"] = safe_cache_key(payload.get("opponent") or opponent)
    payload["prediction_rows"] = list(payload.get("prediction_rows") or [])
    return payload


def clear_cache_files(cache_dir=None):
    """Delete cache files while preserving repository keep files.

    Returns the number of files removed. ``.gitignore`` and ``.gitkeep`` are
    intentionally preserved so the cache directory remains tracked and useful.
    """
    root = Path(cache_dir) if cache_dir is not None else CACHE_DIR
    if not root.exists():
        return 0

    removed = 0
    preserved_names = {".gitignore", ".gitkeep"}
    for path in root.rglob("*"):
        if not path.is_file() or path.name in preserved_names:
            continue
        path.unlink()
        removed += 1
    return removed
