import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error

from data_service import get_player_logs, season_summary
from feature_engineering import prepare_features


class PlayerStatPredictor:
    def __init__(self, player_name, season="2025-26"):
        self.player_name = player_name
        self.season = season
        self.player = None
        self.regular_df = pd.DataFrame()
        self.playoff_df = pd.DataFrame()
        self.model_df = pd.DataFrame()
        self.features = []
        self.opponent_features = []
        self.models = {}

    def load_data(self):
        self.player, self.regular_df, self.playoff_df = get_player_logs(
            self.player_name,
            self.season
        )

        self.model_df, self.features, self.opponent_features = prepare_features(
            self.regular_df,
            self.playoff_df
        )

        if len(self.model_df) < 8:
            raise ValueError("Not enough games to train model.")

    def train(self):
        X = self.model_df[self.features]
        split_index = int(len(self.model_df) * 0.8)

        for stat in ["PTS", "REB", "AST"]:
            y = self.model_df[stat]

            X_train = X.iloc[:split_index]
            X_test = X.iloc[split_index:]
            y_train = y.iloc[:split_index]
            y_test = y.iloc[split_index:]

            model = RandomForestRegressor(n_estimators=300, random_state=42)
            model.fit(X_train, y_train)

            predictions = model.predict(X_test)
            mae = mean_absolute_error(y_test, predictions)

            model.fit(X, y)

            self.models[stat] = {
                "model": model,
                "mae": mae
            }

    def build_next_game_input(self, opponent=None, home=False, playoff_game=False):
        latest = self.model_df.iloc[-1]

        row = {
            "PTS_last": latest["PTS"],
            "REB_last": latest["REB"],
            "AST_last": latest["AST"],
            "MIN_last": latest["MIN"],

            "PTS_avg3": self.model_df["PTS"].tail(3).mean(),
            "REB_avg3": self.model_df["REB"].tail(3).mean(),
            "AST_avg3": self.model_df["AST"].tail(3).mean(),
            "MIN_avg3": self.model_df["MIN"].tail(3).mean(),

            "PTS_avg5": self.model_df["PTS"].tail(5).mean(),
            "REB_avg5": self.model_df["REB"].tail(5).mean(),
            "AST_avg5": self.model_df["AST"].tail(5).mean(),
            "MIN_avg5": self.model_df["MIN"].tail(5).mean(),

            "PTS_trend": self.model_df["PTS"].tail(3).mean() - self.model_df["PTS"].tail(5).mean(),
            "REB_trend": self.model_df["REB"].tail(3).mean() - self.model_df["REB"].tail(5).mean(),
            "AST_trend": self.model_df["AST"].tail(3).mean() - self.model_df["AST"].tail(5).mean(),
            "MIN_trend": self.model_df["MIN"].tail(3).mean() - self.model_df["MIN"].tail(5).mean(),

            "HOME": int(home),
            "PLAYOFF_GAME": int(playoff_game)
        }

        for col in self.opponent_features:
            row[col] = 0

        if opponent:
            opponent_col = f"OPPONENT_{opponent.upper()}"
            if opponent_col in row:
                row[opponent_col] = 1

        return pd.DataFrame([row])[self.features]

    def predict_next_game(self, opponent=None, home=False, playoff_game=False):
        if self.model_df.empty:
            self.load_data()

        if not self.models:
            self.train()

        next_game_input = self.build_next_game_input(
            opponent=opponent,
            home=home,
            playoff_game=playoff_game
        )

        prediction = {}
        prediction_range = {}

        for stat, model_data in self.models.items():
            pred = model_data["model"].predict(next_game_input)[0]
            mae = model_data["mae"]

            prediction[stat] = round(pred, 1)
            prediction_range[stat] = (
                round(pred - mae, 1),
                round(pred + mae, 1)
            )

        return {
            "player": self.player["full_name"],
            "season": self.season,
            "regular_summary": season_summary(self.regular_df),
            "playoff_summary": season_summary(self.playoff_df),
            "prediction": prediction,
            "model_error": {
                stat: round(model_data["mae"], 2)
                for stat, model_data in self.models.items()
            },
            "range": prediction_range,
            "regular_logs": self.regular_df,
            "playoff_logs": self.playoff_df
        }