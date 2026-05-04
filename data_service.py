import pandas as pd
from nba_api.stats.static import players
from nba_api.stats.endpoints import playergamelog

SEASON = "2025-26"

def find_player(player_name):
    matches = players.find_players_by_full_name(player_name)

    if not matches:
        raise ValueError(f"No player found for: {player_name}")

    return matches[0]

def get_player_logs(player_name, season=SEASON):
    player = find_player(player_name)
    player_id = player["id"]

    regular = playergamelog.PlayerGameLog(
        player_id=player_id,
        season=season,
        season_type_all_star="Regular Season"
    ).get_data_frames()[0]

    playoffs = playergamelog.PlayerGameLog(
        player_id=player_id,
        season=season,
        season_type_all_star="Playoffs"
    ).get_data_frames()[0]

    regular["SEASON_TYPE"] = "Regular Season"
    playoffs["SEASON_TYPE"] = "Playoffs"

    df = pd.concat([regular, playoffs], ignore_index=True)

    return player, df