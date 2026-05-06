from datetime import datetime, timedelta

from nba_api.stats.endpoints import scoreboardv2


def _get_team_abbr_from_matchup(matchup):
    # Examples: "LAL vs. BOS" or "OKC @ DEN"
    return matchup.split()[0] if isinstance(matchup, str) and matchup else None


def infer_player_team_from_latest_game(model_df):
    if model_df is None or model_df.empty:
        return None
    latest = model_df.sort_values("GAME_DATE_SORT").iloc[-1]
    return _get_team_abbr_from_matchup(latest.get("MATCHUP"))


def get_next_game_context(player_team, playoff_game=False, lookahead_days=14):
    if not player_team:
        return {
            "opponent": None,
            "home": False,
            "playoff_game": playoff_game,
            "source": "No team inferred, fallback used",
        }

    player_team = player_team.upper().strip()
    today = datetime.utcnow().date()

    for offset in range(0, lookahead_days + 1):
        date_obj = today + timedelta(days=offset)
        date_str = date_obj.strftime("%m/%d/%Y")

        try:
            board = scoreboardv2.ScoreboardV2(game_date=date_str)
            line_score = board.line_score.get_data_frame()
        except Exception:
            continue

        if line_score.empty:
            continue

        day_games = line_score[line_score["TEAM_ABBREVIATION"] == player_team]
        if day_games.empty:
            continue

        game_id = day_games.iloc[0]["GAME_ID"]
        two_teams = line_score[line_score["GAME_ID"] == game_id]
        if len(two_teams) != 2:
            continue

        team_rows = two_teams[["TEAM_ABBREVIATION", "TEAM_CITY_NAME"]].to_dict("records")
        opp = [r["TEAM_ABBREVIATION"] for r in team_rows if r["TEAM_ABBREVIATION"] != player_team]
        opponent = opp[0] if opp else None

        game_header = board.game_header.get_data_frame()
        home = False
        if not game_header.empty:
            header = game_header[game_header["GAME_ID"] == game_id]
            if not header.empty:
                home_team_id = header.iloc[0].get("HOME_TEAM_ID")
                team_game_row = day_games.iloc[0]
                home = int(team_game_row.get("TEAM_ID")) == int(home_team_id)

        return {
            "opponent": opponent,
            "home": home,
            "playoff_game": playoff_game,
            "source": f"scoreboardv2 {date_str}",
        }

    return {
        "opponent": None,
        "home": False,
        "playoff_game": playoff_game,
        "source": "No next game found in lookahead window, fallback used",
    }
