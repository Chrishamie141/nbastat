"""Starter NFL projection logic for props and team markets."""

from models import PredictionResult, SportType
from nfl_data_service import get_nfl_player_pool, get_team_market_placeholders

POSITION_BASELINES = {
    "QB": {"PASS_YDS": 255, "RUSH_YDS": 22, "PASS_TD": 1.8, "TD": 0.15},
    "RB": {"RUSH_YDS": 67, "REC_YDS": 26, "RECEPTIONS": 3.4, "TD": 0.58},
    "WR": {"REC_YDS": 72, "RECEPTIONS": 5.7, "TD": 0.42},
    "TE": {"REC_YDS": 45, "RECEPTIONS": 4.2, "TD": 0.35},
}

STAT_RANGES = {
    "PASS_YDS": 45,
    "RUSH_YDS": 18,
    "REC_YDS": 20,
    "RECEPTIONS": 1.8,
    "TD": 0.35,
    "PASS_TD": 0.55,
}


class NFLPredictor:
    """NFL predictor using clear placeholders until real feeds are wired in."""

    def predict_player(self, player):
        position = player.get("position", "WR")
        baselines = POSITION_BASELINES.get(position, POSITION_BASELINES["WR"])
        results = []
        for stat_type, projection in baselines.items():
            spread = STAT_RANGES.get(stat_type, max(projection * 0.25, 1))
            confidence = 66 if stat_type not in {"TD", "PASS_TD"} else 57
            results.append(
                PredictionResult(
                    sport=SportType.NFL,
                    player=player["player"],
                    team=player.get("team"),
                    opponent=player.get("opponent"),
                    stat_type=stat_type,
                    prediction=float(projection),
                    low_range=max(float(projection) - spread, 0),
                    high_range=float(projection) + spread,
                    confidence=confidence,
                    notes="Placeholder NFL projection. Connect historical stats, matchup, injuries, weather, and odds feeds.",
                )
            )
        return results

    def predict_player_pool(self, team=None):
        rows = []
        for player in get_nfl_player_pool(team=team):
            rows.extend(self.predict_player(player))
        return rows

    def predict_team_markets(self, team):
        return [
            PredictionResult(
                sport=SportType.NFL,
                player=market["team"],
                team=market["team"],
                stat_type=market["market"],
                prediction=0,
                confidence=market["confidence"],
                notes=market["notes"],
            )
            for market in get_team_market_placeholders(team)
        ]
