from predictor import PlayerStatPredictor
from schedule_service import infer_team_from_logs, get_next_game_context

DEFAULT_ROSTER_FILE = "roster.txt"


def run_prediction(player_name, season="2025-26", display=True):
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

    if display:
        print_player_report(result, player_team, context)

    return result, player_team, context


def print_player_report(result, player_team, context):
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

    print("\nMODEL ERROR / CONFIDENCE RANGE")
    print(f"Points: ±{result['model_error']['PTS']}")
    print(f"Rebounds: ±{result['model_error']['REB']}")
    print(f"Assists: ±{result['model_error']['AST']}")

    print("\nPREDICTION RANGE")
    print(f"Points: {result['range']['PTS'][0]} - {result['range']['PTS'][1]}")
    print(f"Rebounds: {result['range']['REB'][0]} - {result['range']['REB'][1]}")
    print(f"Assists: {result['range']['AST'][0]} - {result['range']['AST'][1]}")


def load_roster(file_path=DEFAULT_ROSTER_FILE):
    with open(file_path, "r", encoding="utf-8") as file:
        return [
            line.strip()
            for line in file
            if line.strip() and not line.strip().startswith("#")
        ]


def confidence_label(mae):
    if mae <= 2:
        return "HIGH"
    if mae <= 5:
        return "MEDIUM"
    return "LOW"


def run_roster_predictions(season="2025-26"):
    try:
        player_names = load_roster()
    except FileNotFoundError:
        print(f"\nCould not find {DEFAULT_ROSTER_FILE}. Create it in the same folder as app.py.")
        return

    if not player_names:
        print(f"\n{DEFAULT_ROSTER_FILE} is empty.")
        return

    print("\nRUNNING DEFAULT ROSTER PREDICTIONS")
    print("----------------------------------")
    print(f"Roster File: {DEFAULT_ROSTER_FILE}")
    print(f"Players Found: {len(player_names)}")

    confidence_rankings = []
    successful = 0
    failed = 0

    for player_name in player_names:
        print("\n================================")
        print(f"PLAYER: {player_name}")
        print("================================")

        try:
            result, player_team, context = run_prediction(
                player_name,
                season=season,
                display=True
            )

            for stat in ["PTS", "REB", "AST"]:
                confidence_rankings.append({
                    "player": result["player"],
                    "team": player_team,
                    "stat": stat,
                    "prediction": result["prediction"][stat],
                    "mae": result["model_error"][stat],
                    "range": result["range"][stat],
                    "confidence": confidence_label(result["model_error"][stat])
                })

            successful += 1

        except Exception as error:
            print(f"\nError processing {player_name}: {error}")
            failed += 1

    print("\nBATCH COMPLETE")
    print("--------------")
    print(f"Successful predictions: {successful}")
    print(f"Failed predictions: {failed}")

    print_ranked_confidence(confidence_rankings)


def print_ranked_confidence(confidence_rankings):
    if not confidence_rankings:
        return

    confidence_rankings.sort(key=lambda item: item["mae"])

    print("\nMOST RELIABLE PREDICTIONS")
    print("-------------------------")
    print("Ranked by lowest model error. Lower ± means stronger confidence.\n")

    for index, item in enumerate(confidence_rankings, start=1):
        low, high = item["range"]

        print(
            f"{index}. {item['player']} - {item['stat']} "
            f"Prediction: {item['prediction']} "
            f"| Range: {low} - {high} "
            f"| ±{item['mae']} "
            f"| Confidence: {item['confidence']}"
        )


if __name__ == "__main__":
    print("NBA Player Stat Prediction System")
    print("---------------------------------")
    print("1. Single Player Prediction")
    print("2. Default Roster Prediction")

    mode = input("Select mode 1 or 2: ").strip()

    if mode == "2":
        run_roster_predictions()
    else:
        player_name = input("Enter NBA player name: ").strip()
        run_prediction(player_name)