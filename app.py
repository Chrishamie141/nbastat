import json
from pathlib import Path

from betting_engine import (
    build_parlay,
    parlay_line_threshold,
    print_line_coverage_warning,
    recommend_bets,
    sportsbook_line_coverage,
)
from predictor import PlayerStatPredictor
from roster_service import get_team_roster
from schedule_service import get_next_game_context
from team_utils import normalize_team_abbreviation
from ranking_service import build_rankings, STAT_LABELS, confidence_from_mae
from prediction_tracker import grade_predictions_for_game, save_player_predictions, show_accuracy_report
from prediction_storage import (
    grade_recommendations,
    load_actual_results_from_csv,
    save_bet_recommendations,
    save_prediction_record,
    summarize_graded_bets,
)

DEFAULT_ROSTER_FILE = "roster.txt"
BETTING_LINES_FILE = Path("betting_lines.json")


def run_prediction(player_name, season="2025-26", opponent=None, home=False, playoff_game=False):
    predictor = PlayerStatPredictor(player_name=player_name, season=season)
    predictor.load_data()
    return predictor.predict_next_game(opponent=opponent, home=home, playoff_game=playoff_game)


def prediction_rows_from_result(result, team, context, save_to_db=True):
    """Convert the existing model output into structured stat-level rows."""
    rows = []
    for stat in STAT_LABELS:
        projection = result["blended_prediction"][stat]
        low_range, high_range = result["range"][stat]
        confidence_score, confidence_label = confidence_from_mae(
            projection, result["model_error"][stat], stat
        )
        row = {
            "game_date": context.get("game_date"),
            "team": normalize_team_abbreviation(team),
            "opponent": normalize_team_abbreviation(context.get("opponent")) if context.get("opponent") else None,
            "player": result["player"],
            "stat_type": stat,
            "projection": projection,
            "low_range": low_range,
            "high_range": high_range,
            "confidence_score": confidence_score,
            "confidence_label": confidence_label,
        }
        if save_to_db:
            row["prediction_id"] = save_prediction_record(row)
        rows.append(row)
    return rows


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
    prediction_rows_from_result(result, team, context, save_to_db=True)
    if saved_count:
        print(f"Saved {saved_count} prediction history rows.")
    else:
        print("Prediction history already had these rows; no duplicates saved.")
    print("Saved SQLite prediction rows for betting/grading.")


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


def default_context():
    return {
        "opponent": None,
        "home": False,
        "playoff_game": False,
        "game_date": None,
        "game_id": None,
    }


def run_roster_predictions(roster, team="Unknown", context=None, season="2025-26", emit_output=True):
    context = context or default_context()

    results, failed, prediction_rows = [], [], []
    for player in roster:
        try:
            result = run_prediction(player, season, context["opponent"], context["home"], context["playoff_game"])
            if emit_output:
                print_player_result(result, team, context)
            else:
                prediction_rows.extend(prediction_rows_from_result(result, team, context, save_to_db=True))
            results.append({"player": player, "result": result})
        except Exception as exc:
            failed.append((player, str(exc)))

    if results and emit_output:
        print_top3(build_rankings(results))
    if failed:
        print("\nFailed players:")
        for player, err in failed:
            print(f"- {player}: {err}")

    return {"results": results, "failed": failed, "prediction_rows": prediction_rows}


def run_default_roster_mode(season="2025-26"):
    try:
        with open(DEFAULT_ROSTER_FILE, "r", encoding="utf-8") as roster_file:
            roster = [line.strip() for line in roster_file if line.strip()]
    except FileNotFoundError:
        print(f"Default roster file not found: {DEFAULT_ROSTER_FILE}")
        return

    team = normalize_team_abbreviation(input("Enter team abbreviation for schedule context, or press Enter to skip: "))
    context = None
    if team:
        context = get_next_game_context(team, season=season)
        if context.get("opponent"):
            context["opponent"] = normalize_team_abbreviation(context["opponent"])
        print(f"\nSchedule context: {context['source']}")
    else:
        team = "Unknown"

    run_roster_predictions(roster, team=team, context=context, season=season)


def run_team_mode(season="2025-26"):
    team = normalize_team_abbreviation(input("Enter team abbreviation: "))
    try:
        _, roster = get_team_roster(team, season=season)
    except Exception as exc:
        print(f"Roster lookup failed: {exc}")
        return

    context = get_next_game_context(team, season=season)
    if context.get("opponent"):
        context["opponent"] = normalize_team_abbreviation(context["opponent"])
    print(f"\nSchedule context: {context['source']}")
    run_roster_predictions(roster, team=team, context=context, season=season)


def load_betting_lines(path=BETTING_LINES_FILE):
    if not path.exists():
        print(f"Betting lines file not found: {path}")
        print("Create betting_lines.json with player/stat sportsbook lines before running betting modes.")
        return None
    try:
        with open(path, "r", encoding="utf-8") as lines_file:
            return json.load(lines_file)
    except json.JSONDecodeError as exc:
        print(f"Could not parse {path}: {exc}")
        return None


def _load_roster_for_betting(season="2025-26"):
    team = normalize_team_abbreviation(input("Enter team abbreviation for betting report: "))
    if not team:
        print("A team abbreviation is required.")
        return [], "Unknown", default_context()
    try:
        _, roster = get_team_roster(team, season=season)
    except Exception as exc:
        print(f"Roster lookup failed: {exc}")
        return [], team, default_context()

    context = get_next_game_context(team, season=season)
    if context.get("opponent"):
        context["opponent"] = normalize_team_abbreviation(context["opponent"])
    print(f"\nSchedule context: {context['source']}")

    include_opponent = input("Include opponent roster too? (y/N): ").strip().lower() == "y"
    if include_opponent and context.get("opponent"):
        try:
            opponent = normalize_team_abbreviation(context["opponent"])
            print(f"Opponent roster lookup: {context['opponent']} -> {opponent}")
            _, opponent_roster = get_team_roster(opponent, season=season)
            roster = roster + opponent_roster
        except Exception as exc:
            print(f"Opponent roster lookup failed: {exc}")

    return roster, team, context


def collect_betting_predictions(season="2025-26"):
    roster, team, context = _load_roster_for_betting(season=season)
    if not roster:
        return []
    print("\nRunning predictions for betting engine...")
    run_data = run_roster_predictions(roster, team=team, context=context, season=season, emit_output=False)
    return run_data["prediction_rows"]


def format_percent(value):
    return f"{value * 100:.1f}%"


def print_best_bets_report(recommendations, show_all=False):
    display_bets = recommendations if show_all else [bet for bet in recommendations if bet["recommended"]]
    print("\n========================")
    print("BEST BETS REPORT")
    print("================")
    if not display_bets:
        print("No positive-edge recommended bets found. Choose show-all to inspect avoided/negative-edge props.")
        return
    for index, bet in enumerate(display_bets, 1):
        line_label = int(bet["line"]) if float(bet["line"]).is_integer() else bet["line"]
        print(f"\n{index}. {bet['player']} {line_label}+ {bet['stat_type']}")
        print(f"   Projection: {bet['projection']:.1f}")
        print(f"   Range: {bet['low_range']:.1f} - {bet['high_range']:.1f}")
        print(f"   Model Probability: {format_percent(bet['model_probability'])}")
        print(f"   Book Probability: {format_percent(bet['sportsbook_probability'])}")
        print(f"   Edge: {bet['edge'] * 100:+.1f}%")
        print(f"   Strength: {bet['strength']}")


def run_best_bets_mode(season="2025-26"):
    sportsbook_lines = load_betting_lines()
    if sportsbook_lines is None:
        return []
    predictions = collect_betting_predictions(season=season)
    if not predictions:
        print("No predictions were generated for betting recommendations.")
        return []
    coverage = sportsbook_line_coverage(predictions, sportsbook_lines)
    print_line_coverage_warning(coverage, threshold=10)
    recommendations = recommend_bets(predictions, sportsbook_lines)
    if not recommendations:
        print("No matching sportsbook lines found for the generated predictions.")
        return []
    save_bet_recommendations(recommendations)
    show_all = input("Show all props, including avoid/negative edge? (y/N): ").strip().lower() == "y"
    print_best_bets_report(recommendations, show_all=show_all)
    print(f"\nSaved {len(recommendations)} bet recommendations to predictions.db.")
    return recommendations


def print_parlay(parlay, stake, target_payout):
    print(f"\n========================")
    print(f"{parlay['style']} PARLAY")
    print("===============")
    if not parlay["legs"]:
        print("No qualifying legs were found for this parlay style.")
        if not parlay["complete"]:
            style_label = parlay["style"].title()
            reason = parlay.get("primary_shortfall_reason") or "not enough positive edge bets"
            print("WARNING: betting_lines.json may be too incomplete to build a meaningful parlay.")
            print(
                f"{style_label} parlay requested {parlay['min_legs']}-{parlay['max_legs']} legs, "
                f"but only 0 qualified because {reason}; "
                f"{parlay.get('matched_lines', 0)} sportsbook lines matched current predictions."
            )
        return
    payout = stake * parlay["decimal_odds"]
    print(f"\nStake: ${stake:.2f}")
    print(f"Estimated Odds: {parlay['estimated_odds']:+d}")
    print(f"Estimated Payout: ${payout:.2f}")
    if target_payout and payout < target_payout:
        print(f"Target Payout: ${target_payout:.2f} (not reached with qualifying legs)")
    if not parlay["complete"]:
        print("WARNING: betting_lines.json may be too incomplete to build a meaningful parlay.")
        print(f"Warning: Found {len(parlay['legs'])} legs; target range starts at {parlay['min_legs']}.")
        style_label = parlay["style"].title()
        reason = parlay.get("primary_shortfall_reason") or "not enough positive edge bets"
        print(
            f"{style_label} parlay requested {parlay['min_legs']}-{parlay['max_legs']} legs, "
            f"but only {len(parlay['legs'])} qualified because {reason}; "
            f"{parlay.get('matched_lines', len(parlay['legs']))} sportsbook lines matched current predictions."
        )
    for index, leg in enumerate(parlay["legs"], 1):
        line_label = int(leg["line"]) if float(leg["line"]).is_integer() else leg["line"]
        print(f"{index}. {leg['player']} {line_label}+ {leg['stat_type']} - {format_percent(leg['model_probability'])}")
    print(f"\nCombined Probability: {format_percent(parlay['combined_probability'])}")
    print(f"Risk: {parlay['risk']}")


def run_auto_parlay_mode(season="2025-26"):
    style = input("Choose parlay style (Safe, Balanced, Aggressive): ").strip().upper() or "BALANCED"
    try:
        stake = float(input("Stake amount: $").strip() or "10")
        target_payout = float(input("Target payout (optional, press Enter for none): $").strip() or "0")
    except ValueError:
        print("Stake and target payout must be numbers.")
        return

    sportsbook_lines = load_betting_lines()
    if sportsbook_lines is None:
        return
    predictions = collect_betting_predictions(season=season)
    if not predictions:
        print("No predictions were generated for parlay building.")
        return
    coverage = sportsbook_line_coverage(predictions, sportsbook_lines)
    try:
        coverage_threshold = parlay_line_threshold(style)
    except ValueError as exc:
        print(exc)
        return
    print_line_coverage_warning(coverage, threshold=coverage_threshold)
    recommendations = recommend_bets(predictions, sportsbook_lines)
    save_bet_recommendations(recommendations)
    try:
        parlay = build_parlay(recommendations, style, coverage=coverage)
    except ValueError as exc:
        print(exc)
        return
    print_parlay(parlay, stake, target_payout)
    print(f"\nSaved {len(recommendations)} bet recommendations to predictions.db.")


def _manual_actual_results():
    rows = []
    print("Enter actual results. Leave player blank when finished.")
    while True:
        player = input("Player: ").strip()
        if not player:
            break
        stat_type = input("Stat type (PTS/REB/AST/STL/BLK/3PM): ").strip().upper()
        try:
            actual_result = float(input("Actual result: ").strip())
        except ValueError:
            print("Actual result must be a number; skipping entry.")
            continue
        game_date = input("Game date (YYYY-MM-DD, optional): ").strip()
        rows.append({
            "player": player,
            "stat_type": stat_type,
            "actual_result": actual_result,
            "game_date": game_date,
        })
    return rows


def print_model_grading_report(graded_rows):
    summary = summarize_graded_bets(graded_rows)
    print("\n========================")
    print("MODEL GRADING REPORT")
    print("====================")
    if summary["total"] == 0:
        print("No matching saved recommendations were graded.")
        return
    print(f"\nTotal Bets Graded: {summary['total']}")
    print(f"Hit Rate: {format_percent(summary['hit_rate'])}")
    print(f"ROI: {summary['roi'] * 100:+.1f}%")
    print("\nBy Stat:")
    for stat, hit_rate in sorted(summary["by_stat"].items()):
        print(f"{stat}: {format_percent(hit_rate)}")
    profits = summary["profits_by_stat"]
    most_profitable = sorted(profits.items(), key=lambda item: item[1], reverse=True)
    least_profitable = sorted(profits.items(), key=lambda item: item[1])
    print("\nMost Profitable:")
    for index, (stat, profit) in enumerate(most_profitable[:3], 1):
        print(f"{index}. {stat} (${profit:+.2f})")
    print("\nLeast Profitable:")
    for index, (stat, profit) in enumerate(least_profitable[:3], 1):
        print(f"{index}. {stat} (${profit:+.2f})")


def run_grading_mode():
    print("\nGrade Predictions")
    print("1. Grade saved bet recommendations from CSV")
    print("2. Enter actual results manually")
    print("3. Grade legacy prediction history by NBA game_id")
    print("4. Show legacy model accuracy report")
    choice = input("Select grading option 1, 2, 3, or 4: ").strip()
    if choice == "4":
        show_accuracy_report()
        return
    if choice == "3":
        game_id = input("Enter completed NBA game_id to grade: ").strip()
        if not game_id:
            print("A game_id is required to grade predictions.")
            return
        grade_predictions_for_game(game_id)
        return
    if choice == "1":
        csv_path = input("CSV path (player,stat_type,actual_result,game_date): ").strip()
        try:
            actual_results = load_actual_results_from_csv(csv_path)
        except Exception as exc:
            print(f"Could not load CSV: {exc}")
            return
    elif choice == "2":
        actual_results = _manual_actual_results()
    else:
        print("Invalid grading option.")
        return

    try:
        default_stake = float(input("Stake per bet for P/L grading (default 10): $").strip() or "10")
    except ValueError:
        print("Stake must be a number.")
        return
    graded_rows = grade_recommendations(actual_results, default_stake=default_stake)
    print_model_grading_report(graded_rows)


if __name__ == "__main__":
    print("NBA Player Stat Prediction System")
    print("1. Single Player Prediction")
    print("2. Default roster.txt Prediction")
    print("3. Team Auto-Roster Prediction")
    print("4. Best Bets Report")
    print("5. Auto Parlay Builder")
    print("6. Grade Predictions")
    mode = input("Select mode 1, 2, 3, 4, 5, or 6: ").strip()

    if mode == "6":
        run_grading_mode()
    elif mode == "5":
        run_auto_parlay_mode()
    elif mode == "4":
        run_best_bets_mode()
    elif mode == "3":
        run_team_mode()
    elif mode == "2":
        run_default_roster_mode()
    else:
        player_name = input("Enter NBA player name: ").strip()
        context = default_context()
        result = run_prediction(player_name)
        print_player_result(result, "Unknown", context)
