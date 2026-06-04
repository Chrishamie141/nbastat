from predictor import PlayerStatPredictor
from roster_service import get_team_roster
from schedule_service import get_next_game_context
from ranking_service import build_rankings, STAT_LABELS, confidence_from_mae
from prediction_tracker import grade_predictions_for_game, save_player_predictions, show_accuracy_report

DEFAULT_ROSTER_FILE = "roster.txt"


def run_prediction(player_name, season="2025-26", opponent=None, home=False, playoff_game=False):
    predictor = PlayerStatPredictor(player_name=player_name, season=season)
    predictor.load_data()
    return predictor.predict_next_game(opponent=opponent, home=home, playoff_game=playoff_game)


def print_player_result(result, team, context):
    print(f"\nPlayer: {result['player']}")
    print(f"Team: {team}")
    print(f"Next Opponent: {context['opponent'] or 'General Estimate'}")
    print(f"Home/Away: {'Home' if context['home'] else 'Away/Unknown'}")
    print(f"Game Date: {context['game_date'] or 'Unknown'}")
    print(f"Game ID: {context.get('game_id') or 'Unknown'}")
    print(f"Regular season games: {result['regular_summary']['games']}")
    print(f"Playoff games: {result['playoff_summary']['games']}")
    print(f"Overall averages: {result['overall_summary']}")
    print(f"Opponent-specific averages: {result['opponent_summary']}")
    if context["opponent"] is None:
        print("Opponent-specific model disabled because no opponent was found.")
    else:
        print(f"Opponent-specific sample size: {result['opponent_summary']['games']} games.")
    for stat in STAT_LABELS:
        pred = result['blended_prediction'][stat]
        score, label = confidence_from_mae(pred, result['model_error'][stat], stat)
        print(
            f"{stat} ML: {result['model_prediction'][stat]} | Blended: {pred} | "
            f"Range: {result['range'][stat]} | Reliability: {score} ({label})"
        )

    saved_count = save_player_predictions(result, team, context)
    if saved_count:
        print(f"Saved {saved_count} prediction history rows.")
    else:
        print("Prediction history already had these rows; no duplicates saved.")


def print_top3(rankings):
    for stat, label in STAT_LABELS.items():
        top_reliable = sorted(rankings[stat], key=lambda x: x['score'], reverse=True)[:3]
        print(f"\nTOP 3 MOST RELIABLE {label} PREDICTIONS")
        for i, row in enumerate(top_reliable, 1):
            print(f"{i}. {row['player']} - {row['prediction']} - {row['range'][0]} to {row['range'][1]} - {row['score']} ({row['label']})")

        top_raw = sorted(rankings[stat], key=lambda x: x['prediction'], reverse=True)[:3]
        print(f"TOP 3 HIGHEST {stat} PREDICTIONS")
        for i, row in enumerate(top_raw, 1):
            print(f"{i}. {row['player']} - {row['prediction']}")


def run_roster_predictions(roster, team="Unknown", context=None, season="2025-26"):
    context = context or {
        "opponent": None,
        "home": False,
        "playoff_game": False,
        "game_date": None,
        "game_id": None,
    }

    results, failed = [], []
    for player in roster:
        try:
            result = run_prediction(player, season, context["opponent"], context["home"], context["playoff_game"])
            print_player_result(result, team, context)
            results.append({"player": player, "result": result})
        except Exception as exc:
            failed.append((player, str(exc)))

    if results:
        print_top3(build_rankings(results))
    if failed:
        print("\nFailed players:")
        for player, err in failed:
            print(f"- {player}: {err}")


def run_default_roster_mode(season="2025-26"):
    try:
        with open(DEFAULT_ROSTER_FILE, "r", encoding="utf-8") as roster_file:
            roster = [line.strip() for line in roster_file if line.strip()]
    except FileNotFoundError:
        print(f"Default roster file not found: {DEFAULT_ROSTER_FILE}")
        return

    team = input("Enter team abbreviation for schedule context, or press Enter to skip: ").strip().upper()
    context = None
    if team:
        context = get_next_game_context(team, season=season)
        print(f"\nSchedule context: {context['source']}")
    else:
        team = "Unknown"

    run_roster_predictions(roster, team=team, context=context, season=season)


def run_team_mode(season="2025-26"):
    team = input("Enter team abbreviation: ").strip().upper()
    try:
        _, roster = get_team_roster(team, season=season)
    except Exception as exc:
        print(f"Roster lookup failed: {exc}")
        return

    context = get_next_game_context(team, season=season)
    print(f"\nSchedule context: {context['source']}")
    run_roster_predictions(roster, team=team, context=context, season=season)


def run_grading_mode():
    game_id = input("Enter completed NBA game_id to grade: ").strip()
    if not game_id:
        print("A game_id is required to grade predictions.")
        return
    grade_predictions_for_game(game_id)


if __name__ == "__main__":
    print("NBA Player Stat Prediction System")
    print("1. Single Player Prediction")
    print("2. Default roster.txt Prediction")
    print("3. Team Auto-Roster Prediction")
    print("4. Grade Previous Predictions")
    print("5. Show Model Accuracy Report")
    mode = input("Select mode 1, 2, 3, 4, or 5: ").strip()

    if mode == "5":
        show_accuracy_report()
    elif mode == "4":
        run_grading_mode()
    elif mode == "3":
        run_team_mode()
    elif mode == "2":
        run_default_roster_mode()
    else:
        player_name = input("Enter NBA player name: ").strip()
        context = {"opponent": None, "home": False, "playoff_game": False, "game_date": None, "game_id": None}
        result = run_prediction(player_name)
        print_player_result(result, "Unknown", context)
