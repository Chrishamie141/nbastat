from nba_api.stats.endpoints import leaguegamefinder

def get_next_game_context(player_team, opponent=None, playoff_game=True):
    player_team = player_team.upper().strip()
    opponent = opponent.upper().strip() if opponent else None

    if not opponent:
        return {
            "opponent": None,
            "home": False,
            "playoff_game": playoff_game,
            "source": "No opponent provided"
        }

    gamefinder = leaguegamefinder.LeagueGameFinder(
        team_abbreviation_nullable=player_team
    )

    games = gamefinder.get_data_frames()[0]

    possible_games = games[
        games["MATCHUP"].str.contains(opponent, case=False, na=False)
    ]

    if possible_games.empty:
        return {
            "opponent": opponent,
            "home": False,
            "playoff_game": playoff_game,
            "source": "Opponent not found, defaulting to away"
        }

    latest_matchup = possible_games.iloc[0]["MATCHUP"]

    home = "vs." in latest_matchup

    return {
        "opponent": opponent,
        "home": home,
        "playoff_game": playoff_game,
        "source": latest_matchup
    }