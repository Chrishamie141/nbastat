from nba_api.stats.static import teams
from nba_api.stats.endpoints import commonteamroster

from team_utils import normalize_team_abbreviation
from cache_service import load_roster_cache, save_roster_cache


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
        cached_at = cached.get("cached_at") or "unknown time"
        if cached.get("stale"):
            print(
                f"Cached roster for {team_abbr} is older than {max_age_hours} hours; "
                "using stale cache because live lookup failed."
            )
            status = "STALE CACHE"
        else:
            print(f"Using cached roster for {team_abbr} from {cached_at}.")
            status = "CACHE"
        return get_team_id(team_abbr), cached["roster"], status

    print(f"Roster lookup failed for {team_abbr}: {live_error}")
    return None, [], "UNAVAILABLE"
