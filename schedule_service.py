from nba_api.stats.static import teams


def infer_team_from_logs(regular_df, playoff_df):
    df = playoff_df if playoff_df is not None and not playoff_df.empty else regular_df

    if df is None or df.empty:
        return None

    latest_matchup = df.iloc[0]["MATCHUP"]
    return latest_matchup.split()[0].upper()


def get_next_game_context(player_team, playoff_game=False):
    if not player_team:
        return {
            "opponent": None,
            "home": False,
            "playoff_game": playoff_game,
            "source": "Could not infer player team; used general estimate."
        }

    player_team = player_team.upper().strip()

    valid_teams = [team["abbreviation"] for team in teams.get_teams()]

    if player_team not in valid_teams:
        return {
            "opponent": None,
            "home": False,
            "playoff_game": playoff_game,
            "source": f"Could not validate team {player_team}; used general estimate."
        }

    return {
        "opponent": None,
        "home": False,
        "playoff_game": playoff_game,
        "source": "Schedule API skipped to avoid timeout; used general estimate."
    }