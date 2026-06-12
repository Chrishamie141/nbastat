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
PRESERVED_CACHE_FILENAMES = {".gitignore", ".gitkeep"}
PREDICTION_REQUIRED_FIELDS = ("player", "team", "stat_type", "projection", "low_range", "high_range")
KEY_PLAYERS_BY_TEAM = {
    "NYK": {
        "Jalen Brunson",
        "Karl-Anthony Towns",
        "OG Anunoby",
        "Josh Hart",
        "Mikal Bridges",
    },
    "SAS": {
        "Victor Wembanyama",
        "De'Aaron Fox",
        "Stephon Castle",
        "Dylan Harper",
        "Devin Vassell",
    },
}


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


def _prediction_cache_path(game_date, team, opponent):
    game_date = safe_cache_key(game_date)
    team = safe_cache_key(team)
    opponent = safe_cache_key(opponent)
    return CACHE_DIR / f"predictions_{game_date}_{team}_{opponent}.json"


def _roster_cache_path(team_abbr):
    team_abbr = normalize_team_abbreviation(team_abbr)
    return CACHE_DIR / f"roster_{safe_cache_key(team_abbr)}.json"


def _display_cache_name(path):
    return Path(path).name


def _player_name(value):
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("player", "player_name", "name", "full_name", "PLAYER", "PLAYER_NAME", "DISPLAY_FIRST_LAST"):
            player = value.get(key)
            if player is not None and str(player).strip():
                return str(player).strip()
    return ""


def validate_roster_cache(roster):
    """Return ``(is_valid, reason)`` for roster data before saving or using cache."""
    if not isinstance(roster, list):
        return False, "roster is not a list"
    if len(roster) < 8:
        return False, "roster has fewer than 8 players"
    names = [_player_name(player) for player in roster]
    if sum(1 for name in names if not name) / len(roster) > 0.40:
        return False, "more than 40% of players have missing names"
    if not any(any(char.isalpha() for char in name) for name in names if name):
        return False, "all player names look malformed"
    return True, "valid"


def _normalize_expected_teams(expected_teams):
    if expected_teams is None:
        return []
    return [normalize_team_abbreviation(team) for team in expected_teams if team]


def _expected_players_for_teams(expected_teams, expected_players):
    players = set(expected_players or [])
    for team in _normalize_expected_teams(expected_teams):
        players.update(KEY_PLAYERS_BY_TEAM.get(team, set()))
    return players


def prediction_cache_health_report(prediction_rows, expected_teams=None, expected_players=None):
    """Return health details for cached betting prediction rows."""
    rows_are_list = isinstance(prediction_rows, list)
    rows = prediction_rows if rows_are_list else []
    teams_present = sorted({normalize_team_abbreviation(row.get("team")) for row in rows if isinstance(row, dict) and row.get("team")})
    required_missing_rows = 0
    for row in rows:
        if not isinstance(row, dict) or any(row.get(field) in (None, "") for field in PREDICTION_REQUIRED_FIELDS):
            required_missing_rows += 1
    missing_field_rate = (required_missing_rows / len(rows)) if rows else 1.0
    player_names = {_player_name(row) for row in rows if _player_name(row)}
    expected_player_set = _expected_players_for_teams(expected_teams, expected_players)
    missing_key_players = sorted(player for player in expected_player_set if player not in player_names)

    normalized_expected_teams = _normalize_expected_teams(expected_teams)
    if len(normalized_expected_teams) >= 2:
        minimum_rows = 100
    elif len(normalized_expected_teams) == 1:
        minimum_rows = 50
    else:
        minimum_rows = 50

    failures = []
    if not rows_are_list:
        failures.append("prediction_rows is not a list")
    if len(rows) < minimum_rows:
        failures.append(f"prediction cache has fewer than {minimum_rows} rows")
    missing_teams = sorted(set(normalized_expected_teams) - set(teams_present))
    if missing_teams:
        failures.append(f"missing expected teams: {', '.join(missing_teams)}")
    if missing_key_players:
        failures.append(f"missing key players: {', '.join(missing_key_players)}")
    if missing_field_rate > 0.30:
        failures.append("more than 30% of rows are missing required fields")

    return {
        "valid": not failures,
        "reason": "; ".join(failures) if failures else "valid",
        "rows": len(rows),
        "teams_present": teams_present,
        "key_players_missing": missing_key_players,
        "missing_field_rate": missing_field_rate,
        "minimum_rows": minimum_rows,
        "missing_teams": missing_teams,
    }


def validate_prediction_cache(prediction_rows, expected_teams=None, expected_players=None):
    """Return ``(is_valid, reason)`` for prediction rows."""
    report = prediction_cache_health_report(prediction_rows, expected_teams, expected_players)
    return report["valid"], report["reason"]


def print_prediction_cache_health(report):
    print("Prediction cache health:")
    print(f"* rows: {report.get('rows', 0)}")
    print(f"* teams present: {', '.join(report.get('teams_present') or []) or 'None'}")
    print(f"* key players missing: {', '.join(report.get('key_players_missing') or []) or 'None'}")
    print(f"* missing field rate: {report.get('missing_field_rate', 0):.1%}")


def clear_cache_file(path):
    """Delete one cache file while preserving repo keep files."""
    path = Path(path)
    if path.name in PRESERVED_CACHE_FILENAMES or not path.exists() or not path.is_file():
        return False
    path.unlink()
    return True


def clear_team_cache(team_abbr):
    return clear_cache_file(_roster_cache_path(team_abbr))


def clear_prediction_cache(game_date=None, team=None, opponent=None):
    if game_date is not None and team is not None and opponent is not None:
        return int(clear_cache_file(_prediction_cache_path(game_date, team, opponent)))
    if not CACHE_DIR.exists():
        return 0
    removed = 0
    team_key = safe_cache_key(team) if team is not None else None
    opponent_key = safe_cache_key(opponent) if opponent is not None else None
    date_key = safe_cache_key(game_date) if game_date is not None else None
    for path in CACHE_DIR.glob("predictions_*.json"):
        parts = path.stem.split("_")
        if date_key and date_key not in path.stem:
            continue
        if team_key and team_key not in parts:
            continue
        if opponent_key and opponent_key not in parts:
            continue
        removed += int(clear_cache_file(path))
    return removed


def clear_unhealthy_cache(game_date=None, team=None, opponent=None, expected_teams=None, expected_players=None):
    """Delete invalid matching prediction cache files and return the number removed."""
    paths = []
    if game_date is not None and team is not None and opponent is not None:
        paths = [_prediction_cache_path(game_date, team, opponent)]
    elif CACHE_DIR.exists():
        paths = list(CACHE_DIR.glob("predictions_*.json"))
    removed = 0
    for path in paths:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            if clear_cache_file(path):
                removed += 1
            continue
        report = prediction_cache_health_report(payload.get("prediction_rows"), expected_teams, expected_players)
        if not report["valid"] and clear_cache_file(path):
            removed += 1
    return removed


def _has_invalid_cache_filename(path):
    """Return True when a cache filename contains characters unsafe on Windows."""
    return bool(INVALID_WINDOWS_FILENAME_CHARS.search(Path(path).name))


def _health_report(removed=0, cache_dir_created=False):
    return {"removed": removed, "cache_dir_created": cache_dir_created}


def run_health_check(cache_dir=None, print_summary=False):
    """Validate and heal the cache directory.

    The check is intentionally lightweight for startup use: it creates the cache
    directory if missing, deletes only files known to be unsafe/bad, and leaves
    repository keep files in place. A short summary is printed only when files
    are removed, unless ``print_summary`` is True.
    """
    root = Path(cache_dir) if cache_dir is not None else CACHE_DIR
    cache_dir_created = False
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
        cache_dir_created = True

    removed = 0
    for path in list(root.rglob("*")):
        if not path.is_file() or path.name in PRESERVED_CACHE_FILENAMES:
            continue

        if _has_invalid_cache_filename(path):
            removed += int(clear_cache_file(path))
            continue

        if path.suffix.lower() != ".json":
            continue

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            removed += int(clear_cache_file(path))
            continue

        if path.name.startswith("roster_"):
            roster = payload.get("roster") if isinstance(payload, dict) else None
            valid, _reason = validate_roster_cache(roster)
            if not valid:
                removed += int(clear_cache_file(path))
        elif path.name.startswith("predictions_"):
            rows = payload.get("prediction_rows") if isinstance(payload, dict) else None
            expected_teams = None
            if isinstance(payload, dict):
                expected_teams = [payload.get("team")]
                if payload.get("opponent") not in (None, "", "unknown", "None"):
                    expected_teams.append(payload.get("opponent"))
            valid, _reason = validate_prediction_cache(rows, expected_teams=expected_teams)
            if not valid:
                removed += int(clear_cache_file(path))

    if removed:
        print(f"Startup health check: removed {removed} invalid cache file(s).")
    elif print_summary:
        print("Startup health check: no cache issues found.")
    return _health_report(removed=removed, cache_dir_created=cache_dir_created)


def save_roster_cache(team_abbr, roster):
    """Save a local roster snapshot for one team abbreviation."""
    team_abbr = normalize_team_abbreviation(team_abbr)
    is_valid, reason = validate_roster_cache(roster)
    if not is_valid:
        print(f"Invalid roster cache for {team_abbr}; not saved: {reason}")
        return None
    payload = {
        "team_abbr": team_abbr,
        "cached_at": _utc_now().isoformat(),
        "roster": list(roster or []),
    }
    _write_json(_roster_cache_path(team_abbr), payload)
    return payload


def load_roster_cache(team_abbr, max_age_hours=ROSTER_CACHE_MAX_AGE_HOURS, allow_stale=False):
    """Load a validated roster cache payload, returning None when missing/old/bad."""
    team_abbr = normalize_team_abbreviation(team_abbr)
    path = _roster_cache_path(team_abbr)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as cache_file:
            payload = json.load(cache_file)
    except (OSError, json.JSONDecodeError):
        clear_cache_file(path)
        print(f"Invalid cache detected for {_display_cache_name(path)}; deleted and retrying live lookup.")
        return {"invalid_deleted": True, "roster": [], "team_abbr": team_abbr}

    cached_at = _parse_cached_at(payload.get("cached_at"))
    stale = _is_stale(cached_at, max_age_hours)
    if stale and not allow_stale:
        return None
    payload["team_abbr"] = normalize_team_abbreviation(payload.get("team_abbr") or team_abbr)
    payload["roster"] = list(payload.get("roster") or [])
    is_valid, _reason = validate_roster_cache(payload["roster"])
    if not is_valid:
        clear_cache_file(path)
        print(f"Invalid cache detected for {_display_cache_name(path)}; deleted and retrying live lookup.")
        return {"invalid_deleted": True, "roster": [], "team_abbr": team_abbr}
    payload["stale"] = stale
    payload["cached_at"] = cached_at.isoformat() if cached_at else payload.get("cached_at")
    return payload


def save_prediction_cache(game_date, team, opponent, prediction_rows, expected_teams=None, expected_players=None):
    """Save structured betting prediction rows for one game/team pairing."""
    game_date = safe_cache_key(game_date)
    team = safe_cache_key(team)
    opponent = safe_cache_key(opponent)
    if expected_teams is None:
        expected_teams = [team] + ([] if opponent in ("unknown", "None", None, "") else [opponent])
    is_valid, reason = validate_prediction_cache(prediction_rows, expected_teams=expected_teams, expected_players=expected_players)
    if not is_valid:
        print(f"Invalid prediction cache for {team} vs {opponent}; not saved: {reason}")
        return None
    payload = {
        "game_date": game_date,
        "team": team,
        "opponent": opponent,
        "cached_at": _utc_now().isoformat(),
        "prediction_rows": list(prediction_rows or []),
    }
    _write_json(_prediction_cache_path(game_date, team, opponent), payload)
    return payload


def load_prediction_cache(game_date, team, opponent, max_age_hours=PREDICTION_CACHE_MAX_AGE_HOURS, expected_teams=None, expected_players=None):
    """Load fresh, validated cached betting prediction rows for one game/team pairing."""
    path = _prediction_cache_path(game_date, team, opponent)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as cache_file:
            payload = json.load(cache_file)
    except (OSError, json.JSONDecodeError):
        clear_cache_file(path)
        print(f"Invalid cache detected for {_display_cache_name(path)}; deleted and retrying live lookup.")
        return {"invalid_deleted": True, "prediction_rows": []}

    cached_at = _parse_cached_at(payload.get("cached_at"))
    if _is_stale(cached_at, max_age_hours):
        return None
    payload["cached_at"] = cached_at.isoformat() if cached_at else payload.get("cached_at")
    payload["game_date"] = safe_cache_key(payload.get("game_date") or game_date)
    payload["team"] = safe_cache_key(payload.get("team") or team)
    payload["opponent"] = safe_cache_key(payload.get("opponent") or opponent)
    payload["prediction_rows"] = list(payload.get("prediction_rows") or [])
    if expected_teams is None:
        expected_teams = [payload["team"]]
        if payload["opponent"] not in ("unknown", "None", None, ""):
            expected_teams.append(payload["opponent"])
    report = prediction_cache_health_report(payload["prediction_rows"], expected_teams, expected_players)
    payload["health_report"] = report
    if not report["valid"]:
        clear_cache_file(path)
        print(f"Invalid cache detected for {_display_cache_name(path)}; deleted and retrying live lookup.")
        print_prediction_cache_health(report)
        return {"invalid_deleted": True, "prediction_rows": [], "health_report": report}
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
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        removed += int(clear_cache_file(path))
    return removed
