from nba_api.stats.static import teams
from nba_api.stats.endpoints import commonteamroster


def get_team_id(team_abbreviation):
    team_abbreviation = team_abbreviation.upper().strip()
    for team in teams.get_teams():
        if team["abbreviation"] == team_abbreviation:
            return team["id"]
    raise ValueError(f"Invalid team abbreviation: {team_abbreviation}")


def get_team_roster(team_abbreviation, season="2025-26", timeout=30):
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
