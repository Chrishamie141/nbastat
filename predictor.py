import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error

from data_service import get_player_logs, season_summary, combine_logs, opponent_specific_summary
from feature_engineering import prepare_features, TARGET_STATS


class PlayerStatPredictor:
    def __init__(self, player_name, season="2025-26"):
        self.player_name = player_name
        self.season = season
        self.player = None
        self.regular_df = pd.DataFrame()
        self.playoff_df = pd.DataFrame()
        self.all_logs_df = pd.DataFrame()
        self.model_df = pd.DataFrame()
        self.features = []
        self.opponent_features = []
        self.models = {}

    def load_data(self):
        self.player, self.regular_df, self.playoff_df = get_player_logs(self.player_name, self.season)
        self.all_logs_df = combine_logs(self.regular_df, self.playoff_df)
        self.model_df, self.features, self.opponent_features = prepare_features(self.regular_df, self.playoff_df)
        if len(self.model_df) < 8:
            raise ValueError("Not enough games to train model.")

    def train(self):
        X = self.model_df[self.features]
        split_index = int(len(self.model_df) * 0.8)
        for stat in TARGET_STATS:
            y = self.model_df[stat]
            X_train, X_test = X.iloc[:split_index], X.iloc[split_index:]
            y_train, y_test = y.iloc[:split_index], y.iloc[split_index:]
            model = RandomForestRegressor(n_estimators=300, random_state=42)
            model.fit(X_train, y_train)
            preds = model.predict(X_test)
            mae = mean_absolute_error(y_test, preds)
            model.fit(X, y)
            self.models[stat] = {"model": model, "mae": mae}

    def build_next_game_input(self, opponent=None, home=False, playoff_game=False):
        latest = self.model_df.iloc[-1]
        row = {"HOME": int(home), "PLAYOFF_GAME": int(playoff_game)}
        for s in ["PTS", "REB", "AST", "STL", "BLK", "MIN"]:
            for k in ["last", "avg3", "avg5", "trend"]:
                row[f"{s}_{k}"] = latest[f"{s}_{k}"]
        for col in self.opponent_features:
            row[col] = 0
        if opponent:
            col = f"OPPONENT_{opponent.upper()}"
            if col in row:
                row[col] = 1
        return pd.DataFrame([row])[self.features]

    def predict_next_game(self, opponent=None, home=False, playoff_game=False):
        if self.model_df.empty:
            self.load_data()
        if not self.models:
            self.train()

        next_game_input = self.build_next_game_input(opponent, home, playoff_game)
        prediction, model_prediction, opp_avg, blended, pred_range = {}, {}, {}, {}, {}

        opp_summary = opponent_specific_summary(self.all_logs_df, opponent)
        games_vs_opp = opp_summary["games"]

        for stat, model_data in self.models.items():
            pred = float(model_data["model"].predict(next_game_input)[0])
            mae = float(model_data["mae"])
            model_prediction[stat] = round(pred, 1)
            opponent_avg = float(opp_summary.get(stat, 0.0)) if games_vs_opp > 0 else None
            opp_avg[stat] = round(opponent_avg, 1) if opponent_avg is not None else None

            if games_vs_opp >= 3 and opponent_avg is not None:
                blend = 0.6 * pred + 0.4 * opponent_avg
            elif 1 <= games_vs_opp <= 2 and opponent_avg is not None:
                blend = 0.75 * pred + 0.25 * opponent_avg
            else:
                blend = pred

            blended[stat] = round(blend, 1)
            prediction[stat] = blended[stat]
            pred_range[stat] = (round(max(0, blend - mae), 1), round(blend + mae, 1))

        return {
            "player": self.player["full_name"],
            "season": self.season,
            "regular_summary": season_summary(self.regular_df),
            "playoff_summary": season_summary(self.playoff_df),
            "overall_summary": season_summary(self.all_logs_df),
            "opponent_summary": opp_summary,
            "prediction": prediction,
            "model_prediction": model_prediction,
            "opponent_average": opp_avg,
            "blended_prediction": blended,
            "model_error": {s: round(d["mae"], 2) for s, d in self.models.items()},
            "range": pred_range,
        }
