from nba_api.stats.endpoints import playergamelog

LEBRON_ID = 2544
SEASON = "2025-26"

def get_game_logs(season_type):
    logs = playergamelog.PlayerGameLog(
        player_id=LEBRON_ID,
        season=SEASON,
        season_type_all_star=season_type
    )
    return logs.get_data_frames()[0]

regular = get_game_logs("Regular Season")
playoffs = get_game_logs("Playoffs")

columns = ["GAME_DATE", "MATCHUP", "MIN", "PTS", "REB", "AST", "STL", "BLK", "TOV", "FG_PCT", "FG3_PCT"]

print("\nREGULAR SEASON")
print(regular[columns])

print("\nPLAYOFFS")
print(playoffs[columns])

print("\nTOTAL REGULAR SEASON GAMES:", len(regular))
print("TOTAL PLAYOFF GAMES:", len(playoffs))

print("\nREGULAR SEASON AVERAGES")
print(regular[["PTS", "REB", "AST", "STL", "BLK", "TOV"]].mean().round(1))

print("\nPLAYOFF AVERAGES")
print(playoffs[["PTS", "REB", "AST", "STL", "BLK", "TOV"]].mean().round(1))