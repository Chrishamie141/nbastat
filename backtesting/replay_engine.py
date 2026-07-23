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
            predictions = self.prediction_factory(self.provider, self.config, week)
            frozen: list[tuple[int, dict[str, Any]]] = []
            for prediction in predictions:
                prediction.setdefault("generated_timestamp", utc_now_iso())
                frozen.append((self.store.save_prediction(self.metadata, week, prediction), prediction.copy()))
            outcomes = index_outcomes(getattr(self.provider, "get_outcomes")(self.config.league, self.config.season, week))
            for prediction_id, prediction in frozen:
                key = (prediction.get("game"), prediction.get("market"), prediction.get("player"))
                self.store.grade_prediction(prediction_id, self.grader.grade(prediction, outcomes.get(key)))
            print(f"Week {week} complete")
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
        for game in games:
            players = game.get("players") or []
            for player in players:
                for result in predictor.predict_player(player):
                    if config.normalized_markets() and str(result.stat_type).lower() not in config.normalized_markets():
                        continue
                    predictions.append({
                        "game": game.get("id") or game.get("game"),
                        "prediction": result.prediction,
                        "confidence": result.confidence,
                        "market": result.stat_type,
                        "line": player.get("line"),
                        "reasoning": result.notes,
                        "team": result.team,
                        "player": result.player,
                        "game_type": game.get("game_type"),
                        "home_away": player.get("home_away"),
                    })
        return predictions
