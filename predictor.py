import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error

from data_service import get_player_logs
from feature_engineering import prepare_features
from schedule_service import get_next_game_context, infer_player_team_from_latest_game


class PlayerStatPredictor:
    def __init__(self, player_name, season="2025-26"):
        self.player_name = player_name
        self.season = season
        self.player = None
        self.regular_df = None
        self.playoff_df = None
        self.model_df = None
        self.features = None
        self.opponent_features = None
        self.models = {}

    def load_data(self):
        self.player, self.regular_df, self.playoff_df = get_player_logs(self.player_name, self.season)
        self.model_df, self.features, self.opponent_features = prepare_features(self.regular_df, self.playoff_df)

    def train(self):
        X = self.model_df[self.features]
        for target in ["PTS", "REB", "AST"]:
            y = self.model_df[target]
            split_index = max(int(len(self.model_df) * 0.8), 1)

            X_train, X_test = X.iloc[:split_index], X.iloc[split_index:]
            y_train, y_test = y.iloc[:split_index], y.iloc[split_index:]

            model = RandomForestRegressor(n_estimators=300, random_state=42)
            model.fit(X_train, y_train)

            if len(X_test) > 0:
                preds = model.predict(X_test)
                error = mean_absolute_error(y_test, preds)
            else:
                train_preds = model.predict(X_train)
                error = mean_absolute_error(y_train, train_preds)

            model.fit(X, y)
            self.models[target] = {"model": model, "mae": error}

    def build_next_game_input(self, context):
        latest = self.model_df.iloc[-1]

        row = {
            "PTS_last": latest["PTS"], "REB_last": latest["REB"], "AST_last": latest["AST"], "MIN_last": latest["MIN"],
            "PTS_avg3": self.model_df["PTS"].tail(3).mean(), "REB_avg3": self.model_df["REB"].tail(3).mean(),
            "AST_avg3": self.model_df["AST"].tail(3).mean(), "MIN_avg3": self.model_df["MIN"].tail(3).mean(),
            "PTS_avg5": self.model_df["PTS"].tail(5).mean(), "REB_avg5": self.model_df["REB"].tail(5).mean(),
            "AST_avg5": self.model_df["AST"].tail(5).mean(), "MIN_avg5": self.model_df["MIN"].tail(5).mean(),
            "PTS_trend": self.model_df["PTS"].tail(3).mean() - self.model_df["PTS"].tail(5).mean(),
            "REB_trend": self.model_df["REB"].tail(3).mean() - self.model_df["REB"].tail(5).mean(),
            "AST_trend": self.model_df["AST"].tail(3).mean() - self.model_df["AST"].tail(5).mean(),
            "MIN_trend": self.model_df["MIN"].tail(3).mean() - self.model_df["MIN"].tail(5).mean(),
            "HOME": int(context["home"]), "PLAYOFF_GAME": int(context["playoff_game"]),
        }

        for col in self.opponent_features:
            row[col] = 0

        if context["opponent"]:
            col = f"OPPONENT_{context['opponent'].upper()}"
            if col in row:
                row[col] = 1

        return pd.DataFrame([row])[self.features]

    def predict_next_game(self):
        if self.model_df is None:
            self.load_data()

        self.train()

        inferred_team = infer_player_team_from_latest_game(self.model_df)
        context = get_next_game_context(player_team=inferred_team, playoff_game=False)

        next_game_input = self.build_next_game_input(context)

        prediction = {}
        ranges = {}
        for stat, model_data in self.models.items():
            value = round(model_data["model"].predict(next_game_input)[0], 1)
            prediction[stat] = value
            mae = round(model_data["mae"], 2)
            ranges[stat] = (round(value - mae, 1), round(value + mae, 1))

        return {
            "player": self.player_name,
            "season": self.season,
            "team": inferred_team,
            "context": context,
            "prediction": prediction,
            "model_error": {k: round(v["mae"], 2) for k, v in self.models.items()},
            "prediction_range": ranges,
        }
