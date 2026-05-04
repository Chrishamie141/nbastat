import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error

from data_service import get_player_logs
from feature_engineering import prepare_features
from schedule_service import get_next_game_context

class PlayerStatPredictor:
    def __init__(self, player_name, season="2025-26"):
        self.player_name = player_name
        self.season = season
        self.player = None
        self.df = None
        self.features = None
        self.opponent_features = None
        self.models = {}

    def load_data(self):
        self.player, raw_df = get_player_logs(self.player_name, self.season)
        self.df, self.features, self.opponent_features = prepare_features(raw_df)

    def train(self):
        X = self.df[self.features]

        targets = ["PTS", "REB", "AST"]

        for target in targets:
            y = self.df[target]

            split_index = int(len(self.df) * 0.8)

            X_train = X.iloc[:split_index]
            X_test = X.iloc[split_index:]

            y_train = y.iloc[:split_index]
            y_test = y.iloc[split_index:]

            model = RandomForestRegressor(
                n_estimators=300,
                random_state=42
            )

            model.fit(X_train, y_train)

            predictions = model.predict(X_test)
            error = mean_absolute_error(y_test, predictions)

            model.fit(X, y)

            self.models[target] = {
                "model": model,
                "mae": error
            }

    def build_next_game_input(self, opponent=None, home=False, playoff_game=True):
        context = get_next_game_context(opponent, home, playoff_game)

        latest = self.df.iloc[-1]

        row = {
            "PTS_last": latest["PTS"],
            "REB_last": latest["REB"],
            "AST_last": latest["AST"],
            "MIN_last": latest["MIN"],

            "PTS_avg3": self.df["PTS"].tail(3).mean(),
            "REB_avg3": self.df["REB"].tail(3).mean(),
            "AST_avg3": self.df["AST"].tail(3).mean(),
            "MIN_avg3": self.df["MIN"].tail(3).mean(),

            "PTS_avg5": self.df["PTS"].tail(5).mean(),
            "REB_avg5": self.df["REB"].tail(5).mean(),
            "AST_avg5": self.df["AST"].tail(5).mean(),
            "MIN_avg5": self.df["MIN"].tail(5).mean(),

            "PTS_trend": self.df["PTS"].tail(3).mean() - self.df["PTS"].tail(5).mean(),
            "REB_trend": self.df["REB"].tail(3).mean() - self.df["REB"].tail(5).mean(),
            "AST_trend": self.df["AST"].tail(3).mean() - self.df["AST"].tail(5).mean(),
            "MIN_trend": self.df["MIN"].tail(3).mean() - self.df["MIN"].tail(5).mean(),

            "HOME": context["home"],
            "PLAYOFF_GAME": context["playoff_game"]
        }

        for col in self.opponent_features:
            row[col] = 0

        if opponent:
            opponent_col = f"OPPONENT_{opponent.upper()}"

            if opponent_col in row:
                row[opponent_col] = 1

        return pd.DataFrame([row])[self.features]

    def predict_next_game(self, opponent=None, home=False, playoff_game=True):
        if self.df is None:
            self.load_data()

        self.train()

        next_game_input = self.build_next_game_input(
            opponent=opponent,
            home=home,
            playoff_game=playoff_game
        )

        prediction = {}

        for stat, model_data in self.models.items():
            model = model_data["model"]
            prediction[stat] = round(model.predict(next_game_input)[0], 1)

        return {
            "player": self.player_name,
            "season": self.season,
            "opponent": opponent if opponent else "General Estimate",
            "home": home,
            "playoff_game": playoff_game,
            "prediction": prediction,
            "model_error": {
                stat: round(model_data["mae"], 2)
                for stat, model_data in self.models.items()
            }
        }