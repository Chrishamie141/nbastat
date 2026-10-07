"""NBA player-stat model, isolated from every NFL model implementation."""

from __future__ import annotations

import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error

from .data import combine_logs, get_player_logs, opponent_specific_summary, season_summary
from .features import TARGET_STATS, prepare_features
from .season import active_nba_season


NBA_MODEL_VERSION = "nba-player-stat-v1"


class NBAPlayerStatModel:
    """Current NBA research baseline.

    This is intentionally a complete NBA-owned implementation rather than an
    alias to an NFL or generic sports model.  Future NBA changes can therefore
    be versioned and tested without changing NFL V2.
    """

    model_version = NBA_MODEL_VERSION

    def __init__(self, player_name: str, season: str | int | None = None):
        self.player_name = player_name
        self.season = active_nba_season(season).code
        self.player = None
        self.regular_df = pd.DataFrame()
        self.playoff_df = pd.DataFrame()
        self.all_logs_df = pd.DataFrame()
        self.model_df = pd.DataFrame()
        self.features: list[str] = []
        self.opponent_features: list[str] = []
        self.models: dict[str, dict] = {}

    def load_data(self) -> None:
        self.player, self.regular_df, self.playoff_df = get_player_logs(self.player_name, self.season)
        self.all_logs_df = combine_logs(self.regular_df, self.playoff_df)
        self.model_df, self.features, self.opponent_features = prepare_features(self.regular_df, self.playoff_df)
        if len(self.model_df) < 8:
            raise ValueError("Not enough completed pregame history to train the NBA model.")

    def train(self) -> None:
        inputs = self.model_df[self.features]
        split_index = max(1, int(len(self.model_df) * 0.8))
        for stat in TARGET_STATS:
            target = self.model_df[stat]
            train_x, test_x = inputs.iloc[:split_index], inputs.iloc[split_index:]
            train_y, test_y = target.iloc[:split_index], target.iloc[split_index:]
            model = RandomForestRegressor(n_estimators=300, random_state=42)
            model.fit(train_x, train_y)
            mae = mean_absolute_error(test_y, model.predict(test_x)) if len(test_x) else 0.0
            model.fit(inputs, target)
            self.models[stat] = {"model": model, "mae": float(mae)}

    def build_next_game_input(self, opponent=None, home=False, playoff_game=False):
        latest = self.model_df.iloc[-1]
        row = {"HOME": int(home), "PLAYOFF_GAME": int(playoff_game)}
        for stat in ("PTS", "REB", "AST", "STL", "BLK", "MIN"):
            for key in ("last", "avg3", "avg5", "trend"):
                row[f"{stat}_{key}"] = latest[f"{stat}_{key}"]
        for column in self.opponent_features:
            row[column] = 0
        if opponent:
            column = f"OPPONENT_{str(opponent).upper()}"
            if column in row:
                row[column] = 1
        return pd.DataFrame([row])[self.features]

    def predict_next_game(self, opponent=None, home=False, playoff_game=False):
        if self.model_df.empty:
            self.load_data()
        if not self.models:
            self.train()
        inputs = self.build_next_game_input(opponent, home, playoff_game)
        prediction, raw_prediction, opponent_average, blended, ranges = {}, {}, {}, {}, {}
        opponent_summary = opponent_specific_summary(self.all_logs_df, opponent)
        games_vs_opponent = opponent_summary["games"]
        for stat, model_data in self.models.items():
            raw = float(model_data["model"].predict(inputs)[0])
            mae = float(model_data["mae"])
            raw_prediction[stat] = round(raw, 1)
            opponent_value = float(opponent_summary.get(stat, 0.0)) if games_vs_opponent > 0 else None
            opponent_average[stat] = round(opponent_value, 1) if opponent_value is not None else None
            weight = 0.4 if games_vs_opponent >= 3 else 0.25 if games_vs_opponent else 0.0
            value = raw * (1 - weight) + (opponent_value or 0.0) * weight
            blended[stat] = prediction[stat] = round(value, 1)
            ranges[stat] = (round(max(0, value - mae), 1), round(value + mae, 1))
        return {
            "player": self.player["full_name"], "season": self.season,
            "model_version": self.model_version,
            "regular_summary": season_summary(self.regular_df),
            "playoff_summary": season_summary(self.playoff_df),
            "overall_summary": season_summary(self.all_logs_df),
            "opponent_summary": opponent_summary, "prediction": prediction,
            "model_prediction": raw_prediction, "opponent_average": opponent_average,
            "blended_prediction": blended,
            "model_error": {stat: round(value["mae"], 2) for stat, value in self.models.items()},
            "range": ranges,
        }
