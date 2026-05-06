from predictor import PlayerStatPredictor


def _print_summary(title, df):
    print(f"\n{title}")
    print("-" * len(title))
    if df is None or df.empty:
        print("No games available.")
        return
    print(f"Games: {len(df)}")
    means = df[["PTS", "REB", "AST", "MIN"]].mean().round(1)
    print(f"PTS: {means['PTS']} | REB: {means['REB']} | AST: {means['AST']} | MIN: {means['MIN']}")


def run_prediction(player_name):
    predictor = PlayerStatPredictor(player_name)
    result = predictor.predict_next_game()

    print("\nNBA PLAYER STAT PREDICTION")
    print("--------------------------")
    print(f"Player: {result['player']}")
    print(f"Season: {result['season']}")
    print(f"Team (inferred): {result['team']}")
    print(f"Opponent (next): {result['context']['opponent'] or 'General Estimate'}")
    print(f"Home Game: {result['context']['home']}")
    print(f"Schedule Source: {result['context']['source']}")

    _print_summary("Regular Season Summary", predictor.regular_df)
    _print_summary("Playoff Summary", predictor.playoff_df)

    print("\nPredicted Next Game Stats")
    for stat in ["PTS", "REB", "AST"]:
        lo, hi = result["prediction_range"][stat]
        print(f"{stat}: {result['prediction'][stat]} (range: {lo} to {hi})")

    print("\nModel Error (MAE)")
    print(f"PTS MAE: {result['model_error']['PTS']}")
    print(f"REB MAE: {result['model_error']['REB']}")
    print(f"AST MAE: {result['model_error']['AST']}")


if __name__ == "__main__":
    player_name = input("Enter NBA player name: ").strip()
    try:
        run_prediction(player_name)
    except Exception as exc:
        print(f"\nCould not complete prediction: {exc}")
