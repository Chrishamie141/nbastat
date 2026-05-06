from predictor import PlayerStatPredictor
from schedule_service import infer_team_from_logs, get_next_game_context


def run_prediction(player_name, season="2025-26"):
    predictor = PlayerStatPredictor(player_name=player_name, season=season)

    predictor.load_data()

    player_team = infer_team_from_logs(
        predictor.regular_df,
        predictor.playoff_df
    )

    playoff_game = not predictor.playoff_df.empty

    context = get_next_game_context(
        player_team=player_team,
        playoff_game=playoff_game
    )

    result = predictor.predict_next_game(
        opponent=context["opponent"],
        home=context["home"],
        playoff_game=context["playoff_game"]
    )

    print("\nNBA PLAYER STAT REPORT")
    print("----------------------")
    print(f"Player: {result['player']}")
    print(f"Season: {result['season']}")
    print(f"Team: {player_team}")
    print(f"Opponent: {context['opponent'] if context['opponent'] else 'General Estimate'}")
    print(f"Home Game: {context['home']}")
    print(f"Playoff Game: {context['playoff_game']}")
    print(f"Schedule Context: {context['source']}")

    regular = result["regular_summary"]
    print("\nREGULAR SEASON")
    print(f"Games Played: {regular['games']}")
    print(f"PTS: {regular['PTS']}")
    print(f"REB: {regular['REB']}")
    print(f"AST: {regular['AST']}")

    playoffs = result["playoff_summary"]
    print("\nPLAYOFFS")
    if playoffs["games"] == 0:
        print("No playoff games available.")
    else:
        print(f"Games Played: {playoffs['games']}")
        print(f"PTS: {playoffs['PTS']}")
        print(f"REB: {playoffs['REB']}")
        print(f"AST: {playoffs['AST']}")

    print("\nPREDICTED NEXT GAME")
    print(f"Points: {result['prediction']['PTS']}")
    print(f"Rebounds: {result['prediction']['REB']}")
    print(f"Assists: {result['prediction']['AST']}")

    print("\nMODEL ERROR")
    print(f"Points MAE: {result['model_error']['PTS']}")
    print(f"Rebounds MAE: {result['model_error']['REB']}")
    print(f"Assists MAE: {result['model_error']['AST']}")

    print("\nPREDICTION RANGE")
    print(f"Points: {result['range']['PTS'][0]} - {result['range']['PTS'][1]}")
    print(f"Rebounds: {result['range']['REB'][0]} - {result['range']['REB'][1]}")
    print(f"Assists: {result['range']['AST'][0]} - {result['range']['AST'][1]}")


if __name__ == "__main__":
    player_name = input("Enter NBA player name: ").strip()
    run_prediction(player_name)