def compute_rankings(player_results: list[dict], excluded_players: set[str] | None = None) -> dict:
    excluded_players = excluded_players or set()
    eligible = [row for row in player_results if row["player"] not in excluded_players]

    if not eligible:
        return {"eligible": [], "top_total": [], "highest": {"PTS": None, "REB": None, "AST": None}}

    for row in eligible:
        pred = row["prediction"]
        row["team_total"] = round(pred["PTS"] + pred["REB"] + pred["AST"], 1)

    top_total = sorted(eligible, key=lambda r: r["team_total"], reverse=True)[:3]

    highest = {}
    for stat in ["PTS", "REB", "AST"]:
        highest[stat] = max(eligible, key=lambda r: r["prediction"][stat])

    return {"eligible": eligible, "top_total": top_total, "highest": highest}
