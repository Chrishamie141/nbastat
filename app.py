from predictor import PlayerStatPredictor
from schedule_service import get_next_game_context

def run_prediction(player_name, player_team, opponent=None, playoff_game=True):
    context = get_next_game_context(
        player_team=player_team,
        opponent=opponent,
        playoff_game=playoff_game
    )

    predictor = PlayerStatPredictor(player_name)

    result = predictor.predict_next_game(
        opponent=context["opponent"],
        home=context["home"],
        playoff_game=context["playoff_game"]
    )

    print("\nNBA PLAYER STAT PREDICTION")
    print("--------------------------")
    print(f"Player: {result['player']}")
    print(f"Season: {result['season']}")
    print(f"Opponent: {result['opponent']}")
    print(f"Home Game: {result['home']}")
    print(f"Playoff Game: {result['playoff_game']}")
    print(f"Schedule Source: {context['source']}")

    print("\nPredicted Next Game Stats")
    print(f"Points: {result['prediction']['PTS']}")
    print(f"Rebounds: {result['prediction']['REB']}")
    print(f"Assists: {result['prediction']['AST']}")

    print("\nModel Error")
    print(f"Points MAE: {result['model_error']['PTS']}")
    print(f"Rebounds MAE: {result['model_error']['REB']}")
    print(f"Assists MAE: {result['model_error']['AST']}")

if __name__ == "__main__":
    player_name = input("Enter player name: ")
    player_team = input("Enter player's team abbreviation ex: LAL, OKC, BOS: ")
    opponent = input("Enter opponent abbreviation ex: OKC, LAL, BOS: ")

    playoff_answer = input("Is this a playoff game? y/n: ").lower().strip()
    playoff_game = playoff_answer == "y"

    run_prediction(
        player_name=player_name,
        player_team=player_team,
        opponent=opponent,
        playoff_game=playoff_game
    )