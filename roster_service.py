import time
from pathlib import Path

from nba_api.stats.static import teams
from nba_api.stats.endpoints import commonallplayers, commonteamroster
from nba_api.stats.library import http

from team_utils import normalize_team_abbreviation
import cache_service
from cache_service import clear_team_cache, load_roster_cache, safe_cache_key, save_roster_cache


PLAYER_NAME_KEYS = (
    "player_name",
    "name",
    "full_name",
    "PLAYER",
    "PLAYER_NAME",
    "DISPLAY_FIRST_LAST",
)
PLAYER_ID_KEYS = ("player_id", "id", "PLAYER_ID", "PERSON_ID")

STATS_NBA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
}
http.NBAStatsHTTP.headers.update(STATS_NBA_HEADERS)
ROSTER_LOOKUP_TIMEOUT = 15
ROSTER_LOOKUP_RETRIES = 3
DEFAULT_ROSTER_FILE = Path("roster.txt")


def _error_type(exc):
    root = exc.__cause__ or exc
    return type(root).__name__


def _stats_headers():
    """Return NBA Stats headers and keep nba_api's global header config in sync."""
    http.NBAStatsHTTP.headers.update(STATS_NBA_HEADERS)
    return dict(http.NBAStatsHTTP.headers)


def _extract_player_names(roster_df, preferred_columns):
    for column in preferred_columns:
        if column in roster_df.columns:
            return roster_df[column].dropna().astype(str).str.strip().tolist()
    raise ValueError(f"Roster response missing player-name columns: {list(roster_df.columns)}")


def _live_roster_lookup(team_abbreviation, season="2025-26", timeout=ROSTER_LOOKUP_TIMEOUT):
    """Call the original stats.nba.com commonteamroster endpoint."""
    team_id = get_team_id(team_abbreviation)
    roster_df = commonteamroster.CommonTeamRoster(
        team_id=team_id,
        season=season,
        league_id_nullable="00",
        headers=_stats_headers(),
        timeout=timeout,
    ).get_data_frames()[0]
    if roster_df.empty:
        raise ValueError("Empty roster response")
    names = _extract_player_names(roster_df, ("PLAYER", "PLAYER_NAME", "DISPLAY_FIRST_LAST"))
    return team_id, names


def _live_roster_lookup_commonallplayers(team_abbreviation, season="2025-26", timeout=ROSTER_LOOKUP_TIMEOUT):
    """Fallback live roster lookup using nba_api's current-season all-player endpoint."""
    normalized = normalize_team_abbreviation(team_abbreviation)
    team_id = get_team_id(normalized)
    players_df = commonallplayers.CommonAllPlayers(
        is_only_current_season=1,
        league_id="00",
        season=season,
        headers=_stats_headers(),
        timeout=timeout,
    ).get_data_frames()[0]
    if players_df.empty:
        raise ValueError("Empty commonallplayers response")

    filtered = players_df
    if "TEAM_ID" in filtered.columns:
        filtered = filtered[filtered["TEAM_ID"].astype(str) == str(team_id)]
    elif "TEAM_ABBREVIATION" in filtered.columns:
        filtered = filtered[filtered["TEAM_ABBREVIATION"].astype(str) == normalized]
    else:
        raise ValueError(f"commonallplayers response cannot be filtered by team: {list(players_df.columns)}")

    if filtered.empty:
        raise ValueError(f"Empty commonallplayers roster response for {normalized}")
    names = _extract_player_names(filtered, ("DISPLAY_FIRST_LAST", "PLAYER_NAME", "PLAYER", "PERSON_NAME"))
    return team_id, names

def _load_local_roster_file(path=DEFAULT_ROSTER_FILE):
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as roster_file:
        return [line.strip() for line in roster_file if line.strip()]


def get_player_display_name(player):
    """Return a normalized display name from supported roster entry formats."""
    if isinstance(player, str):
        return player.strip()
    if isinstance(player, dict):
        for key in PLAYER_NAME_KEYS:
            value = player.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return ""


def _has_player_id(player):
    if not isinstance(player, dict):
        return False
    return any(player.get(key) not in (None, "") for key in PLAYER_ID_KEYS)


def _name_looks_malformed(name):
    cleaned = str(name or "").strip()
    if len(cleaned) < 2:
        return True
    if not any(char.isalpha() for char in cleaned):
        return True
    if cleaned.startswith(("{", "[")) or cleaned.endswith(("}", "]")):
        return True
    if ":" in cleaned or "<" in cleaned or ">" in cleaned:
        return True
    return False


def validate_roster_cache(roster):
    """Return ``(is_valid, reason)`` for cached roster payloads.

    Validation catches empty, too-small, malformed, or structurally mismatched
    roster caches before they can generate misleading prediction rows.
    """
    if not isinstance(roster, list):
        return False, "roster is not a list"
    if len(roster) < 8:
        return False, "roster has fewer than 8 players"

    names = [get_player_display_name(player) for player in roster]
    missing_count = sum(1 for name in names if not name)
    if missing_count / len(roster) > 0.40:
        return False, "more than 40% of players have missing names"

    present_names = [name for name in names if name]
    if present_names and all(_name_looks_malformed(name) for name in present_names):
        return False, "all player names look malformed"

    dict_entries = [player for player in roster if isinstance(player, dict)]
    if dict_entries and not any(_has_player_id(player) for player in dict_entries):
        return True, "valid; player IDs unavailable"

    return True, "valid"


def _print_cached_roster_diagnostics(team_abbr, cached, validation):
    roster = cached.get("roster") or []
    cached_at = cached.get("cached_at") or "unknown time"
    path = cache_service.CACHE_DIR / f"roster_{safe_cache_key(team_abbr)}.json"
    names = [get_player_display_name(player) for player in roster if get_player_display_name(player)]
    sample = ", ".join(names[:5]) if names else "None"
    print(f"Using cached roster for {team_abbr} from {path} (cached at {cached_at}).")
    print(f"Cached roster players: {len(roster)}")
    print(f"Sample: {sample}")
    print(f"Roster cache validation: {'PASS' if validation[0] else 'FAIL'}")
    if not validation[0]:
        print(f"Roster cache validation reason: {validation[1]}")


def get_team_id(team_abbreviation):
    team_abbreviation = normalize_team_abbreviation(team_abbreviation)
    for team in teams.get_teams():
        if team["abbreviation"] == team_abbreviation:
            return team["id"]
    raise ValueError(f"Invalid team abbreviation: {team_abbreviation}")


def get_team_roster(team_abbreviation, season="2025-26", timeout=ROSTER_LOOKUP_TIMEOUT):
    """Fetch a team roster from the original stats.nba.com endpoint path."""
    team_abbreviation = normalize_team_abbreviation(team_abbreviation)
    try:
        return _live_roster_lookup(team_abbreviation, season=season, timeout=timeout)
    except Exception as exc:
        raise ValueError(f"Roster lookup failed for {team_abbreviation}: {exc}") from exc


def _retry_backoff(attempt):
    return attempt


def _try_live_roster_with_retries(team_abbr, season, timeout, attempts=ROSTER_LOOKUP_RETRIES):
    last_error = None
    endpoints = (
        ("commonteamroster", get_team_roster),
        ("commonallplayers", _live_roster_lookup_commonallplayers),
    )
    for endpoint_name, lookup in endpoints:
        for attempt in range(1, attempts + 1):
            try:
                print(
                    f"Live roster lookup for {team_abbr} using {endpoint_name} "
                    f"(attempt {attempt}/{attempts}, timeout={timeout}s, headers=yes)."
                )
                return (*lookup(team_abbr, season=season, timeout=timeout), None)
            except Exception as exc:
                last_error = exc
                if attempt < attempts:
                    print(f"Roster lookup attempt {attempt} failed for {team_abbr} via {endpoint_name}; retrying...")
                    print(
                        f"Diagnostics: team={team_abbr}; endpoint={endpoint_name}; season={season}; "
                        f"timeout={timeout}; headers_applied=yes; error_type={_error_type(exc)}; retry=True"
                    )
                    time.sleep(_retry_backoff(attempt))
                else:
                    next_step = "trying commonallplayers live fallback" if endpoint_name == "commonteamroster" else "trying cache"
                    print(
                        f"Live roster lookup failed for {team_abbr} using {endpoint_name} after {attempts} "
                        f"attempts: {_error_type(exc)}; {next_step}."
                    )
                    print(
                        f"Diagnostics: team={team_abbr}; endpoint={endpoint_name}; season={season}; "
                        f"timeout={timeout}; headers_applied=yes; error_type={_error_type(exc)}; retry=False"
                    )
    return None, [], last_error

def _print_roster_lookup_failure_summary(team_abbr, attempts, live_error, cached=None, cache_invalid_deleted=False):
    cache_used = bool(cached and cached.get("roster"))
    cache_state = "used" if cache_used else "unavailable"
    if cache_invalid_deleted:
        cache_state = "invalid/deleted"
    print(
        f"Roster lookup failed for {team_abbr} after {attempts} attempts. "
        f"Cache {cache_state}."
    )
    print(
        f"Roster diagnostics: normalized_team={team_abbr}; live_attempts={attempts}; "
        f"error_type={_error_type(live_error) if live_error else 'None'}; "
        f"cache_used={cache_used}; cache_invalid_deleted={cache_invalid_deleted}"
    )
    if cache_used:
        names = [get_player_display_name(player) for player in cached.get("roster", []) if get_player_display_name(player)]
        print(f"Roster cache sample players: {', '.join(names[:5]) or 'None'}")


def get_roster_with_cache(team_abbr, season="2025-26", timeout=ROSTER_LOOKUP_TIMEOUT, max_age_hours=72):
    """Return ``(team_id, roster, status)`` using cache only after live retries fail.

    Status is one of LIVE, CACHE, STALE CACHE, INVALID-DELETED, or UNAVAILABLE.
    """
    team_abbr = normalize_team_abbreviation(team_abbr)
    team_id, roster, live_error = _try_live_roster_with_retries(team_abbr, season, timeout)
    if roster:
        save_roster_cache(team_abbr, roster)
        return team_id, roster, "LIVE"

    cached = load_roster_cache(team_abbr, max_age_hours=max_age_hours, allow_stale=True)
    if cached and cached.get("invalid_deleted"):
        print(f"Invalid cached roster for {team_abbr} deleted; retrying live lookup one more time.")
        try:
            team_id, roster = get_team_roster(team_abbr, season=season, timeout=timeout)
            save_roster_cache(team_abbr, roster)
            return team_id, roster, "LIVE"
        except Exception as exc:
            live_error = exc
            print(f"Live retry after cache deletion failed for {team_abbr}: {_error_type(exc)}")
        _print_roster_lookup_failure_summary(team_abbr, ROSTER_LOOKUP_RETRIES + 1, live_error, cached=cached, cache_invalid_deleted=True)
        return None, [], "INVALID-DELETED"

    if cached and cached.get("roster"):
        validation = validate_roster_cache(cached["roster"])
        _print_cached_roster_diagnostics(team_abbr, cached, validation)
        if validation[0]:
            if cached.get("stale"):
                print(
                    f"Cached roster for {team_abbr} is older than {max_age_hours} hours; "
                    "using stale cache because live lookup failed."
                )
                status = "STALE CACHE"
            else:
                status = "CACHE"
            return get_team_id(team_abbr), cached["roster"], status
        clear_team_cache(team_abbr)
        print(f"Invalid cache detected for roster_{safe_cache_key(team_abbr)}.json; deleted and retrying live lookup.")

    _print_roster_lookup_failure_summary(team_abbr, ROSTER_LOOKUP_RETRIES, live_error, cached=cached)
    return None, [], "UNAVAILABLE"


def debug_roster_live_lookup(team_abbr, season="2025-26", timeout=ROSTER_LOOKUP_TIMEOUT):
    """Print uncached live roster diagnostics for one team."""
    normalized = normalize_team_abbreviation(team_abbr)
    headers = _stats_headers()
    try:
        team_id = get_team_id(normalized)
    except Exception as exc:
        team_id = None
        print("Debug Live Roster Lookup")
        print(f"Normalized team abbreviation: {normalized}")
        print(f"Team ID: {team_id}")
        print(f"Endpoint attempted: none")
        print(f"Season: {season}")
        print(f"Timeout: {timeout}")
        print(f"Headers applied: {'yes' if headers else 'no'}")
        print("Attempt count: 0")
        print("Result count: 0")
        print("First 10 player names: None")
        print(f"Error type: {_error_type(exc)}")
        print(f"Error message: {exc}")
        return {"team": normalized, "team_id": team_id, "count": 0, "status": "FAILED", "valid": False}

    print("Debug Live Roster Lookup")
    print(f"Normalized team abbreviation: {normalized}")
    print(f"Team ID: {team_id}")
    print(f"Season: {season}")
    print(f"Timeout: {timeout}")
    print(f"Headers applied: {'yes' if headers else 'no'}")
    print("Cache bypassed: yes")

    endpoints = (
        ("commonteamroster", get_team_roster),
        ("commonallplayers", _live_roster_lookup_commonallplayers),
    )
    total_attempts = 0
    last_error = None
    attempted = []
    for endpoint_name, lookup in endpoints:
        for attempt in range(1, ROSTER_LOOKUP_RETRIES + 1):
            total_attempts += 1
            attempted.append(endpoint_name)
            print(f"Endpoint attempted: {endpoint_name}")
            print(f"Attempt count: {attempt}/{ROSTER_LOOKUP_RETRIES} ({total_attempts} total)")
            try:
                _, roster = lookup(normalized, season=season, timeout=timeout)
                print(f"Result count: {len(roster)}")
                print(f"First 10 player names: {', '.join(roster[:10]) or 'None'}")
                print("Status: LIVE")
                return {
                    "team": normalized,
                    "team_id": team_id,
                    "count": len(roster),
                    "status": "LIVE",
                    "valid": True,
                    "attempts": total_attempts,
                    "endpoints": attempted,
                }
            except Exception as exc:
                last_error = exc
                print(f"Error type: {_error_type(exc)}")
                print(f"Error message: {exc}")
                if attempt < ROSTER_LOOKUP_RETRIES:
                    time.sleep(_retry_backoff(attempt))

    print("Result count: 0")
    print("First 10 player names: None")
    print("Status: FAILED")
    return {
        "team": normalized,
        "team_id": team_id,
        "count": 0,
        "status": "FAILED",
        "valid": False,
        "attempts": total_attempts,
        "endpoints": attempted,
        "error": str(last_error) if last_error else None,
    }

def debug_roster_lookup(team_abbr, season="2025-26"):
    """Print diagnostics for one roster lookup without requiring app internals."""
    normalized = normalize_team_abbreviation(team_abbr)
    print("Debug Roster Lookup")
    print(f"Normalized team abbreviation: {normalized}")
    try:
        team_id, roster = get_team_roster(normalized, season=season)
        print(f"Live roster result count: {len(roster)}")
        print(f"First 10 player names: {', '.join(roster[:10]) or 'None'}")
        save_roster_cache(normalized, roster)
        print("Cache status: LIVE")
        print("Validation result: PASS")
        return {"team": normalized, "count": len(roster), "status": "LIVE", "valid": True}
    except Exception as exc:
        print(f"Live roster lookup failed for {normalized} using stats.nba.com: {_error_type(exc)}")
    cached = load_roster_cache(normalized, allow_stale=True)
    if cached and cached.get("roster"):
        valid, reason = validate_roster_cache(cached["roster"])
        names = [get_player_display_name(player) for player in cached["roster"] if get_player_display_name(player)]
        print(f"Live roster result count: 0")
        print(f"First 10 player names: {', '.join(names[:10]) or 'None'}")
        print("Cache status: CACHE")
        print(f"Validation result: {'PASS' if valid else 'FAIL'} - {reason}")
        return {"team": normalized, "count": 0, "status": "CACHE", "valid": valid}
    status = "INVALID-DELETED" if cached and cached.get("invalid_deleted") else "UNAVAILABLE"
    print("Live roster result count: 0")
    print("First 10 player names: None")
    print(f"Cache status: {status}")
    print("Validation result: FAIL - no valid roster cache")
    return {"team": normalized, "count": 0, "status": status, "valid": False}
