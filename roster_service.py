from nba_api.stats.static import teams
from nba_api.stats.endpoints import commonteamroster

from team_utils import normalize_team_abbreviation
import cache_service
from cache_service import load_roster_cache, safe_cache_key, save_roster_cache


PLAYER_NAME_KEYS = (
    "player_name",
    "name",
    "full_name",
    "PLAYER",
    "PLAYER_NAME",
    "DISPLAY_FIRST_LAST",
)
PLAYER_ID_KEYS = ("player_id", "id", "PLAYER_ID", "PERSON_ID")


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


def get_team_roster(team_abbreviation, season="2025-26", timeout=30):
    team_abbreviation = normalize_team_abbreviation(team_abbreviation)
    team_id = get_team_id(team_abbreviation)
    try:
        roster_df = commonteamroster.CommonTeamRoster(
            team_id=team_id,
            season=season,
            timeout=timeout,
        ).get_data_frames()[0]
        if roster_df.empty:
            raise ValueError("Empty roster response")
        names = roster_df["PLAYER"].dropna().tolist()
        return team_id, names
    except Exception as exc:
        raise ValueError(f"Roster lookup failed for {team_abbreviation}: {exc}") from exc


def get_roster_with_cache(team_abbr, season="2025-26", timeout=30, max_age_hours=72):
    """Return ``(team_id, roster, status)`` using local cache when live lookup fails.

    Status is one of LIVE, CACHE, STALE CACHE, or UNAVAILABLE.
    """
    team_abbr = normalize_team_abbreviation(team_abbr)
    try:
        team_id, roster = get_team_roster(team_abbr, season=season, timeout=timeout)
        save_roster_cache(team_abbr, roster)
        return team_id, roster, "LIVE"
    except Exception as exc:
        live_error = exc

    cached = load_roster_cache(team_abbr, max_age_hours=max_age_hours, allow_stale=True)
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
        print(f"Ignoring invalid cached roster for {team_abbr}; live lookup failed too.")

    print(f"Roster lookup failed for {team_abbr}: {live_error}")
    return None, [], "UNAVAILABLE"
