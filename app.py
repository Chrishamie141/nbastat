import argparse
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
from roster_service import debug_roster_live_lookup, debug_roster_lookup, get_player_display_name, get_team_roster, get_roster_with_cache
from schedule_service import get_next_game_context
from team_utils import normalize_team_abbreviation
from cache_service import (
    clear_cache_files,
    load_prediction_cache,
    prediction_cache_health_report,
    print_prediction_cache_health,
    run_health_check,
    save_prediction_cache,
)
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
        player_name = get_player_display_name(player)
        if not player_name:
            failed.append((player, "player lookup error: missing player name"))
            continue
        try:
            result = run_prediction(player_name, season, context["opponent"], context["home"], context["playoff_game"])
            if emit_output:
                print_player_result(result, team, context)
            else:
                prediction_rows.extend(prediction_rows_from_result(result, team, context, save_to_db=True))
            results.append({"player": player_name, "result": result})
        except Exception as exc:
            failed.append((player_name, str(exc)))

    if results and emit_output:
        print_top3(build_rankings(results))
    if failed:
        print("\nFailed players:")
        for player, err in failed:
            print(f"- {get_player_display_name(player) or player}: {err}")

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
        _, roster, roster_status = get_roster_with_cache(team, season=season)
    except Exception as exc:
        print(f"Roster lookup failed: {exc}")
        return
    if not roster:
        print("Roster lookup failed: no live roster or valid cache available.")
        return
    print(f"Roster source: {roster_status}")

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
    cache_status = {
        "rosters": {},
        "predictions": "LIVE",
        "opponent_unavailable": False,
        "cached_roster_players": [],
        "prediction_health": None,
    }
    if not team:
        print("A team abbreviation is required.")
        return [], "Unknown", default_context(), cache_status

    context = get_next_game_context(team, season=season)
    if context.get("opponent"):
        context["opponent"] = normalize_team_abbreviation(context["opponent"])
    print(f"\nSchedule context: {context['source']}")

    _, roster, team_status = get_roster_with_cache(team, season=season)
    cache_status["rosters"][team] = team_status
    if team_status not in ("LIVE", "CACHE", "STALE CACHE"):
        roster = []
    if "CACHE" in team_status:
        cache_status["cached_roster_players"].extend(
            get_player_display_name(player) for player in roster
        )
    if not roster:
        return [], team, context, cache_status

    include_opponent = input("Include opponent roster too? (y/N): ").strip().lower() == "y"
    if include_opponent and context.get("opponent"):
        opponent = normalize_team_abbreviation(context["opponent"])
        print(f"Opponent roster lookup: {context['opponent']} -> {opponent}")
        _, opponent_roster, opponent_status = get_roster_with_cache(opponent, season=season)
        cache_status["rosters"][opponent] = opponent_status
        if opponent_status not in ("LIVE", "CACHE", "STALE CACHE"):
            opponent_roster = []
        if opponent_roster:
            if "CACHE" in opponent_status:
                cache_status["cached_roster_players"].extend(
                    get_player_display_name(player) for player in opponent_roster
                )
            roster = roster + opponent_roster
        else:
            cache_status["opponent_unavailable"] = True
            print("Opponent unavailable; building selected-team-only parlay.")

    return roster, team, context, cache_status


def _expected_prediction_teams(team, context, include_opponent=True):
    expected = [normalize_team_abbreviation(team)] if team else []
    opponent = context.get("opponent")
    if include_opponent and opponent:
        expected.append(normalize_team_abbreviation(opponent))
    return expected


def _load_cached_betting_predictions(team, context, cache_status, include_opponent=True):
    """Return valid cached betting predictions when live roster/prediction generation is unavailable."""
    expected_teams = _expected_prediction_teams(team, context, include_opponent=include_opponent)
    cached = load_prediction_cache(
        context.get("game_date"),
        team,
        context.get("opponent"),
        expected_teams=expected_teams or None,
    )
    if not cached or cached.get("invalid_deleted"):
        cache_status["predictions"] = "UNAVAILABLE"
        if cached and cached.get("health_report"):
            cache_status["prediction_health"] = cached["health_report"]
        return []

    predictions = cached.get("prediction_rows") or []
    cache_status["predictions"] = "CACHE"
    cache_status["prediction_health"] = cached.get("health_report") or prediction_cache_health_report(
        predictions, expected_teams=expected_teams or None
    )
    print("Using cached betting predictions because live roster/prediction generation is unavailable.")
    return predictions


def collect_betting_predictions(season="2025-26"):
    roster, team, context, cache_status = _load_roster_for_betting(season=season)
    if not roster:
        predictions = _load_cached_betting_predictions(team, context, cache_status)
        if predictions:
            return predictions, cache_status
        print("No valid live roster, roster cache, or prediction cache available. Cannot build reliable parlay.")
        return [], cache_status

    if cache_status.get("opponent_unavailable"):
        print("Opponent unavailable; building selected-team-only parlay.")

    print("\nRunning predictions for betting engine...")
    run_data = run_roster_predictions(roster, team=team, context=context, season=season, emit_output=False)
    predictions = run_data["prediction_rows"]
    if predictions:
        expected_teams = sorted({normalize_team_abbreviation(row.get("team")) for row in predictions if row.get("team")})
        save_prediction_cache(
            context.get("game_date"),
            team,
            context.get("opponent"),
            predictions,
            expected_teams=expected_teams or _expected_prediction_teams(team, context, include_opponent=False),
        )
        cache_status["predictions"] = "LIVE"
        cache_status["prediction_health"] = prediction_cache_health_report(
            predictions, expected_teams=expected_teams or [team]
        )
    else:
        predictions = _load_cached_betting_predictions(
            team, context, cache_status, include_opponent=not cache_status.get("opponent_unavailable")
        )
        if not predictions:
            print("No live predictions were generated and no valid prediction cache is available.")
    return predictions, cache_status


def print_cache_status(cache_status):
    print("\nCache status:")
    for team, status in cache_status.get("rosters", {}).items():
        print(f"* {team} roster: {status}")
    print(f"* Predictions: {cache_status.get('predictions', 'LIVE')}")
    if cache_status.get("prediction_health"):
        print_prediction_cache_health(cache_status["prediction_health"])


def format_percent(value):
    return f"{value * 100:.1f}%"


def high_probability_note(probability):
    if probability > 0.85:
        return "NOTE: High model probability. Verify this line is not too low or based on stale odds."
    return None


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
        note = high_probability_note(bet["model_probability"])
        if note:
            print(f"   {note}")


def run_best_bets_mode(season="2025-26"):
    sportsbook_lines = load_betting_lines()
    if sportsbook_lines is None:
        return []
    predictions, cache_status = collect_betting_predictions(season=season)
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
    print_cache_status(cache_status)
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
    if parlay.get("conservative_warning"):
        print(
            "WARNING: Could not build a true SAFE parlay from available lines. "
            "Showing best available conservative legs."
        )
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
        print(
            f"{index}. {leg['player']} {line_label}+ {leg['stat_type']} - "
            f"{format_percent(leg['model_probability'])} | Quality: {leg.get('quality_score', 0):.0f}"
        )
        note = high_probability_note(leg["model_probability"])
        if note:
            print(f"   {note}")
    print(f"\nRaw Combined Probability: {format_percent(parlay['raw_combined_probability'])}")
    print(f"Adjusted Combined Probability: {format_percent(parlay['adjusted_combined_probability'])}")
    if parlay["style"] == "AGGRESSIVE" and parlay["adjusted_combined_probability"] > 0.15:
        print("WARNING: Aggressive parlay probability may still be overestimated. Treat as directional, not guaranteed.")
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
    predictions, cache_status = collect_betting_predictions(season=season)
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
    print_cache_status(cache_status)
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


def run_clear_cache_mode():
    removed_count = clear_cache_files()
    print(f"Removed {removed_count} cache file(s).")


def run_debug_roster_lookup_mode(team, season="2025-26"):
    if not team:
        print("A team abbreviation is required.")
        return
    debug_roster_lookup(team, season=season)


def run_debug_roster_live_lookup_mode(team, season="2025-26"):
    if not team:
        print("A team abbreviation is required.")
        return
    debug_roster_live_lookup(team, season=season)


def run_debug_player_mode(player_name, season="2025-26"):
    clean_name = str(player_name or "").strip()
    print("Debug Player Prediction")
    print(f"Player name received: {clean_name}")
    print(f"Season: {season}")
    try:
        result = run_prediction(clean_name, season)
    except Exception as exc:
        print("Regular season data found: False")
        print("Regular season games count: 0")
        print("Playoff games count: 0")
        print(f"Error: {exc}")
        return {"player": clean_name, "season": season, "regular_found": False, "error": str(exc)}

    regular_games = result.get("regular_summary", {}).get("games", 0)
    playoff_games = result.get("playoff_summary", {}).get("games", 0)
    print(f"Regular season data found: {regular_games > 0}")
    print(f"Regular season games count: {regular_games}")
    print(f"Playoff games count: {playoff_games}")
    return {
        "player": clean_name,
        "season": season,
        "regular_found": regular_games > 0,
        "regular_games": regular_games,
        "playoff_games": playoff_games,
    }


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


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="NBA Player Stat Prediction System")
    parser.add_argument("--clear-cache", action="store_true", help="Clear cache files and exit.")
    parser.add_argument("--debug-roster", metavar="TEAM", help="Run roster diagnostics for a team and exit.")
    parser.add_argument("--debug-roster-live", metavar="TEAM", help="Run uncached live roster diagnostics for a team and exit.")
    parser.add_argument("--debug-player", metavar="PLAYER", help="Run direct player prediction diagnostics and exit.")
    parser.add_argument("--health-check", action="store_true", help="Run cache health check and exit.")
    return parser.parse_args(argv)


def print_main_menu():
    print("NBA Player Stat Prediction System")
    print("1. Single Player Prediction")
    print("2. Default roster.txt Prediction")
    print("3. Team Auto-Roster Prediction")
    print("4. Best Bets Report")
    print("5. Auto Parlay Builder")
    print("6. Grade Predictions")


def main(argv=None):
    args = parse_args(argv)
    if args.clear_cache:
        run_clear_cache_mode()
        return
    if args.debug_roster:
        run_debug_roster_lookup_mode(args.debug_roster)
        return
    if args.debug_roster_live:
        run_debug_roster_live_lookup_mode(args.debug_roster_live)
        return
    if args.debug_player:
        run_debug_player_mode(args.debug_player)
        return
    if args.health_check:
        run_health_check(print_summary=True)
        return

    run_health_check()
    print_main_menu()
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


if __name__ == "__main__":
    main()
