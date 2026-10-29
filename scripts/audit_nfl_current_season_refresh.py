"""Chronological static-vs-refreshed NFL V2 comparison (read-only)."""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtesting.nfl_game_predictor import NFLGameMarketPredictorV2
from backend.app.services.nfl_product_service import _schedule


def dt(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def team_rows(game):
    completed = dt(game["kickoff_time"]) + timedelta(hours=6)
    stamp = completed.isoformat()
    return [
        {"season": 2026, "week": game["week"], "game_id": game["game_id"],
         "team": game["home_team"], "opponent": game["away_team"], "home_away": "home",
         "points_for": game["home_score"], "points_against": game["away_score"],
         "completed_at": stamp, "data_as_of": stamp,
         "record_role": "completed_game_history", "is_pregame": False},
        {"season": 2026, "week": game["week"], "game_id": game["game_id"],
         "team": game["away_team"], "opponent": game["home_team"], "home_away": "away",
         "points_for": game["away_score"], "points_against": game["home_score"],
         "completed_at": stamp, "data_as_of": stamp,
         "record_role": "completed_game_history", "is_pregame": False},
    ]


def metrics(rows, key):
    decisions = [row for row in rows if row["actual"] != "TIE" and row[key] is not None]
    correct = [float(row[f"{key}_winner"] == row["actual"]) for row in decisions]
    home_y = [float(row["actual"] == row["home_team"]) for row in decisions]
    home_p = [float(row[key]) for row in decisions]
    brier = sum((p - y) ** 2 for p, y in zip(home_p, home_y)) / len(decisions)
    log_loss = -sum(y * math.log(max(p, 1e-15)) + (1-y) * math.log(max(1-p, 1e-15))
                    for p, y in zip(home_p, home_y)) / len(decisions)
    calibration = []
    for low in [0.5, 0.55, 0.60, 0.65, 0.70, 0.80]:
        high = {0.5: 0.55, 0.55: 0.60, 0.60: 0.65, 0.65: 0.70, 0.70: 0.80, 0.80: 1.01}[low]
        indices = [i for i, row in enumerate(decisions)
                   if low <= max(float(row[key]), 1-float(row[key])) < high]
        if indices:
            confidence = sum(max(home_p[i], 1-home_p[i]) for i in indices) / len(indices)
            accuracy = sum(correct[i] for i in indices) / len(indices)
            calibration.append({"low": low, "high": min(high, 1.0), "n": len(indices),
                                "confidence": confidence, "accuracy": accuracy,
                                "absolute_error": abs(confidence-accuracy)})
    ece = sum(bucket["n"] * bucket["absolute_error"] for bucket in calibration) / len(decisions)
    n, wins = len(decisions), sum(correct)
    z = 1.959963984540054
    center = (wins/n + z*z/(2*n)) / (1 + z*z/n)
    radius = z * math.sqrt((wins/n)*(1-wins/n)/n + z*z/(4*n*n)) / (1 + z*z/n)
    return {"games": len(rows), "decisions": len(decisions), "ties": len(rows)-len(decisions),
            "wins": int(sum(correct)), "accuracy": sum(correct)/len(decisions),
            "accuracy95CI": [center-radius, center+radius],
            "brier": brier, "log_loss": log_loss, "ece": ece, "calibration": calibration}


def main():
    baseline = json.loads((ROOT / "data/nfl_team_game_history.json").read_text(encoding="utf-8"))["items"]
    games = []
    for week in range(1, 8):
        for game in _schedule(2026, week, "regular"):
            if game["status"] == "final" and game["home_score"] is not None and game["away_score"] is not None:
                games.append({**game, "week": week, "display_week": week, "provider_week": week,
                              "week_key": f"REG{week}", "week_label": f"Week {week}"})
    games.sort(key=lambda row: (row["kickoff_time"], row["game_id"]))
    completed_2026 = []
    predictor = NFLGameMarketPredictorV2()
    rows = []
    for game in games:
        static_projection = predictor.project(game, baseline)
        refreshed_projection = predictor.project(game, baseline + completed_2026)
        actual = "TIE" if game["home_score"] == game["away_score"] else (
            game["home_team"] if game["home_score"] > game["away_score"] else game["away_team"])
        row = {"game_id": game["game_id"], "week": game["week"],
               "kickoff_time": game["kickoff_time"], "home_team": game["home_team"],
               "away_team": game["away_team"], "actual": actual}
        for name, projection in (("static", static_projection), ("refreshed", refreshed_projection)):
            probability = None if projection is None else projection.probability(
                "h2h", game["home_team"], home_team=game["home_team"], away_team=game["away_team"])
            row[name] = probability
            row[f"{name}_winner"] = None if probability is None else (
                game["home_team"] if probability >= .5 else game["away_team"])
        rows.append(row)
        completed_2026.extend(team_rows(game))
    decision_rows = [row for row in rows if row["actual"] != "TIE"]
    improved = sum(row["static_winner"] != row["actual"] and row["refreshed_winner"] == row["actual"]
                   for row in decision_rows)
    regressed = sum(row["static_winner"] == row["actual"] and row["refreshed_winner"] != row["actual"]
                    for row in decision_rows)
    discordant = improved + regressed
    mcnemar_p = min(1.0, 2 * sum(math.comb(discordant, index) for index in range(min(improved, regressed)+1))
                     / (2 ** discordant)) if discordant else 1.0
    summary = {"method": "strictly chronological; 2026 finals become available at kickoff+6h",
               "marketMetrics": "unavailable; no verified timestamped prices were used",
               "static": metrics(rows, "static"), "refreshed": metrics(rows, "refreshed"),
               "pickChanges": sum(row["static_winner"] != row["refreshed_winner"] for row in rows),
               "changedFromLossToWin": improved, "changedFromWinToLoss": regressed,
               "exactMcNemarP": mcnemar_p}
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
