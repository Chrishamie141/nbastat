from nba_api.stats.static import teams
from nba_api.stats.endpoints import commonteamroster


def get_team_roster(team_abbreviation: str, season: str = "2025-26") -> list[str]:
    abbr = team_abbreviation.upper().strip()
    team = next((t for t in teams.get_teams() if t["abbreviation"] == abbr), None)
    if team is None:
        raise ValueError(f"Unknown team abbreviation: {team_abbreviation}")

    roster_df = commonteamroster.CommonTeamRoster(team_id=team["id"], season=season).get_data_frames()[0]
    return roster_df["PLAYER"].dropna().tolist()
