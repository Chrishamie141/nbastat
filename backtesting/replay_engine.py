"""Chronological replay engine for isolated internal backtesting."""

from __future__ import annotations

from typing import Any, Callable

from nfl_predictor import NFLPredictor

from .config import BacktestConfig
from .grader import PredictionGrader, index_outcomes
from .historical_provider import HistoricalSnapshotProvider, PredictionDataProvider
from .metrics import MetricsCalculator
from .prediction_store import PredictionStore
from .reports import ReportExporter
from .utils import utc_now_iso
from .versioning import RunMetadata, create_run_metadata

PredictionFactory = Callable[[PredictionDataProvider, BacktestConfig, int], list[dict[str, Any]]]


class ReplayEngine:
    """Simulate a season chronologically while preventing future data leakage."""

    def __init__(self, config: BacktestConfig, provider: PredictionDataProvider | None = None, prediction_factory: PredictionFactory | None = None):
        self.config = config
        self.provider = provider or HistoricalSnapshotProvider(config.data_dir)
        self.prediction_factory = prediction_factory or self._production_prediction_adapter
        self.store = PredictionStore(config.db_path)
        self.grader = PredictionGrader()
        self.metrics = MetricsCalculator()
        self.metadata: RunMetadata = create_run_metadata(config)

    def run(self) -> dict[str, Any]:
        """Execute the replay, grade predictions, export reports, and return a summary."""
        self.store.create_run(self.metadata)
        for week in self._weeks():
            games = self.provider.get_games(self.config.league, self.config.season, week)
            odds = self.provider.get_odds(self.config.league, self.config.season, week)
            predictions = self.prediction_factory(self.provider, self.config, week)
            frozen: list[tuple[int, dict[str, Any]]] = []
            for prediction in predictions:
                prediction.setdefault("generated_timestamp", utc_now_iso())
                frozen.append((self.store.save_prediction(self.metadata, week, prediction), prediction.copy()))
            outcomes_raw = getattr(self.provider, "get_outcomes")(self.config.league, self.config.season, week)
            outcomes = index_outcomes(outcomes_raw)
            graded_count = 0
            for prediction_id, prediction in frozen:
                key = (prediction.get("game"), prediction.get("market"), prediction.get("player"))
                grade = self.grader.grade(prediction, outcomes.get(key))
                if grade.get("correct") is not None:
                    graded_count += 1
                self.store.grade_prediction(prediction_id, grade)
            print(f"Week {week}:")
            print(f"- Games loaded: {len(games)}")
            print(f"- Odds loaded: {len(odds)}")
            print(f"- Predictions generated: {len(predictions)}")
            print(f"- Outcomes loaded: {len(outcomes_raw)}")
            print(f"- Predictions graded: {graded_count}")
        stored = self.store.load_predictions(self.metadata.run_id)
        metrics = self.metrics.calculate(stored)
        report_dir = None
        if self.config.export:
            report_dir = ReportExporter(self.config.results_dir).export(self.metadata, stored, metrics)
        summary = {"run_id": self.metadata.run_id, "metrics": metrics, "report_dir": str(report_dir) if report_dir else None}
        print(f"Final report: {summary}")
        return summary

    def _weeks(self) -> range:
        start = self.config.start_week or 1
        end = self.config.end_week or start
        return range(start, end + 1)

    def _production_prediction_adapter(self, provider: PredictionDataProvider, config: BacktestConfig, week: int) -> list[dict[str, Any]]:
        """Adapt existing production NFL predictor output to backtest prediction rows."""
        if config.league.lower() != "nfl":
            return []
        predictor = NFLPredictor()
        predictions: list[dict[str, Any]] = []
        games = provider.get_games(config.league, config.season, week)
        odds = provider.get_odds(config.league, config.season, week)
        player_stats = provider.get_player_stats(config.league, config.season, week)
        odds_by_game = {}
        for odd in odds:
            odds_by_game.setdefault(odd.get("game_id"), []).append(odd)
        for game in games:
            players = game.get("players") or []
            if not players:
                teams = {game.get("home_team"), game.get("away_team")}
                players = [p for p in player_stats if p.get("team") in teams]
            for player in players:
                for result in predictor.predict_player(player):
                    stat_type = str(result.stat_type)
                    if config.normalized_markets() and stat_type.lower() not in config.normalized_markets():
                        continue
                    matching_odds = [o for o in odds_by_game.get(game.get("game_id") or game.get("id") or game.get("game"), []) if str(o.get("market")).lower() == stat_type.lower() and (not o.get("selection") or o.get("selection") == result.player)]
                    line = (matching_odds[0].get("line") if matching_odds else player.get("line"))
                    pick = "over" if line is not None and float(result.prediction) > float(line) else result.prediction
                    predictions.append({
                        "game": game.get("game_id") or game.get("id") or game.get("game"),
                        "prediction": pick,
                        "confidence": result.confidence,
                        "market": stat_type,
                        "line": line,
                        "reasoning": result.notes,
                        "team": result.team,
                        "player": result.player,
                        "game_type": game.get("game_type"),
                        "home_away": player.get("home_away"),
                    })
        return predictions
