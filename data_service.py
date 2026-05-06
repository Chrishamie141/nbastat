import pandas as pd
from nba_api.stats.static import players
from nba_api.stats.endpoints import playergamelog

SEASON = "2025-26"


def find_player(player_name):
    player_name = player_name.strip()
    matches = players.find_players_by_full_name(player_name)

    if not matches:
        raise ValueError(f"No player found for: {player_name}")

    exact_matches = [
        player for player in matches
        if player["full_name"].lower() == player_name.lower()
    ]

    return exact_matches[0] if exact_matches else matches[0]


def safe_player_log(player_id, season, season_type):
    try:
        df = playergamelog.PlayerGameLog(
            player_id=player_id,
            season=season,
            season_type_all_star=season_type
        ).get_data_frames()[0]
    except Exception:
        df = pd.DataFrame()

    if not df.empty:
        df["SEASON_TYPE"] = season_type

    return df


def get_player_logs(player_name, season=SEASON):
    player = find_player(player_name)
    player_id = player["id"]

    regular_df = safe_player_log(
        player_id=player_id,
        season=season,
        season_type="Regular Season"
    )

    playoff_df = safe_player_log(
        player_id=player_id,
        season=season,
        season_type="Playoffs"
    )

    print("\n[DATA CHECK]")
    print(f"Player: {player['full_name']}")
    print(f"Regular Season Games: {len(regular_df)}")
    print(f"Playoff Games: {len(playoff_df)}")

    if regular_df.empty:
        raise ValueError(
            f"No regular season data found for {player['full_name']} in {season}"
        )

    return player, regular_df, playoff_df


def season_summary(df):
    if df is None or df.empty:
        return {
            "games": 0,
            "PTS": 0.0,
            "REB": 0.0,
            "AST": 0.0
        }

    return {
        "games": int(len(df)),
        "PTS": round(float(df["PTS"].mean()), 1),
        "REB": round(float(df["REB"].mean()), 1),
        "AST": round(float(df["AST"].mean()), 1)
    }


def recent_logs(df, limit=5):
    if df is None or df.empty:
        return pd.DataFrame()

    columns = ["GAME_DATE", "MATCHUP", "MIN", "PTS", "REB", "AST"]
    available_columns = [col for col in columns if col in df.columns]

    return df[available_columns].head(limit)