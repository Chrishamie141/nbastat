from predictor import PlayerStatPredictor
from schedule_service import get_next_game_context
from roster_service import get_team_roster
from ranking_service import compute_rankings
from injury_service import InjuryService


def run_single_player_prediction(player_name, player_team, opponent=None, playoff_game=True, injury_service=None):
    injury_service = injury_service or InjuryService()
    if injury_service.is_injured(player_name):
        print(f"\nWARNING: {injury_service.canonical_name(player_name)} is marked as OUT in injured_players.txt")

    context = get_next_game_context(
        player_team=player_team,
        opponent=opponent,
        playoff_game=playoff_game,
    )

    predictor = PlayerStatPredictor(player_name)
    result = predictor.predict_next_game(
        opponent=context["opponent"],
        home=context["home"],
        playoff_game=context["playoff_game"],
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


def run_team_auto_roster_mode(team, opponent=None, playoff_game=True):
    injury_service = InjuryService()
    roster = get_team_roster(team)
    results = []
    excluded = []

    context = get_next_game_context(player_team=team, opponent=opponent, playoff_game=playoff_game)

    for player_name in roster:
        if injury_service.is_injured(player_name):
            excluded.append(injury_service.canonical_name(player_name))
            print(f"OUT: {player_name}")
            continue

        predictor = PlayerStatPredictor(player_name)
        try:
            result = predictor.predict_next_game(
                opponent=context["opponent"],
                home=context["home"],
                playoff_game=context["playoff_game"],
            )
            results.append(result)
        except Exception as exc:
            print(f"Skipping {player_name}: {exc}")

    ranking = compute_rankings(results)

    print("\nTEAM TOP 3 (PTS+REB+AST)")
    for idx, row in enumerate(ranking["top_total"], start=1):
        print(f"{idx}. {row['player']} - {row['team_total']}")

    print("\nHIGHEST STAT RANKINGS")
    for stat, row in ranking["highest"].items():
        if row:
            print(f"{stat}: {row['player']} ({row['prediction'][stat]})")

    print("\nOUT / EXCLUDED PLAYERS")
    for name in excluded:
        print(f"- {name}")


if __name__ == "__main__":
    mode = input("Mode: single or team_auto_roster? ").strip().lower()

    playoff_answer = input("Is this a playoff game? y/n: ").lower().strip()
    playoff_game = playoff_answer == "y"

    if mode == "team_auto_roster":
        team = input("Enter team abbreviation ex: LAL, OKC, BOS: ")
        opponent = input("Enter opponent abbreviation ex: OKC, LAL, BOS: ")
        run_team_auto_roster_mode(team=team, opponent=opponent, playoff_game=playoff_game)
    else:
        player_name = input("Enter player name: ")
        player_team = input("Enter player's team abbreviation ex: LAL, OKC, BOS: ")
        opponent = input("Enter opponent abbreviation ex: OKC, LAL, BOS: ")
        run_single_player_prediction(
            player_name=player_name,
            player_team=player_team,
            opponent=opponent,
            playoff_game=playoff_game,
        )
