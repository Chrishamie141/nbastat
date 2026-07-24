"""Chronological replay engine for isolated internal backtesting."""

from __future__ import annotations

from typing import Any, Callable

from nfl_predictor import NFLPredictor

from .config import BacktestConfig, PredictionMode
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
            mode = self.config.mode()
            if mode is PredictionMode.BETTING and not odds:
                predictions = []
                print("- Replay mode: BETTING")
                print("- Predictions unavailable: no historical odds were loaded; current/sample odds were not substituted")
            else:
                predictions = self.prediction_factory(self.provider, self.config, week)
                print(f"- Replay mode: {mode.value}")
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
            diag = self._odds_diagnostics(games, odds, predictions)
            print(f"- Odds loaded: {len(odds)}")
            print(f"- Historical odds loaded: {len(odds)}")
            print(f"- Bookmakers: {', '.join(diag['bookmakers']) if diag['bookmakers'] else 'none'}")
            print(f"- Markets: {', '.join(diag['markets']) if diag['markets'] else 'none'}")
            print(f"- Lines skipped: {diag['lines_skipped']}")
            print(f"- Events skipped: {diag['events_skipped']}")
            print(f"- Reason skipped: {diag['reason_skipped']}")
            print(f"- Games without odds: {diag['games_without_odds']}")
            print(f"- Games with incomplete markets: {diag['games_with_incomplete_markets']}")
            print(f"- Predictions generated: {len(predictions)}")
            print(f"- Outcomes loaded: {len(outcomes_raw)}")
            print(f"- Predictions graded: {graded_count}")
        stored = self.store.load_predictions(self.metadata.run_id)
        metrics = self.metrics.calculate(stored)
        report_dir = None
        if self.config.export:
            report_dir = ReportExporter(self.config.results_dir).export(self.metadata, stored, metrics)
        summary = {"run_id": self.metadata.run_id, "mode": self.config.mode().value, "metrics": metrics, "report_dir": str(report_dir) if report_dir else None}
        print(f"Final report: {summary}")
        return summary

    def _weeks(self) -> range:
        start = self.config.start_week or 1
        end = self.config.end_week or start
        return range(start, end + 1)

    def _odds_diagnostics(self, games: list[dict[str, Any]], odds: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
        game_ids = {g.get("game_id") or g.get("id") or g.get("game") for g in games}
        odds_game_ids = {o.get("game_id") for o in odds}
        markets = sorted({str(o.get("market")) for o in odds if o.get("market")})
        required = {str(p.get("market")) for p in predictions if p.get("market")}
        incomplete = [gid for gid in game_ids if gid in odds_game_ids and required and not required.issubset({str(o.get("market")) for o in odds if o.get("game_id") == gid})]
        return {
            "bookmakers": sorted({str(o.get("sportsbook") or o.get("bookmaker")) for o in odds if o.get("sportsbook") or o.get("bookmaker")}),
            "markets": markets,
            "lines_skipped": sum(1 for p in predictions if p.get("line") is None),
            "events_skipped": len([gid for gid in odds_game_ids if gid not in game_ids]),
            "reason_skipped": "missing_historical_odds" if not odds else "missing_line_or_market_match" if any(p.get("line") is None for p in predictions) else "none",
            "games_without_odds": sorted(gid for gid in game_ids if gid not in odds_game_ids),
            "games_with_incomplete_markets": sorted(incomplete),
        }

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
                    if config.mode() is PredictionMode.BETTING and not matching_odds:
                        continue
                    odds_row = matching_odds[0] if matching_odds else {}
                    edge = None
                    try:
                        edge = float(result.prediction) - float(line) if line is not None else None
                    except Exception:
                        edge = None
                    predictions.append({
                        "game": game.get("game_id") or game.get("id") or game.get("game"),
                        "prediction": pick,
                        "confidence": result.confidence,
                        "market": stat_type,
                        "line": line,
                        "sportsbook_odds": odds_row.get("odds"),
                        "sportsbook": odds_row.get("sportsbook"),
                        "edge": edge,
                        "clv": odds_row.get("closing_line_value") or odds_row.get("clv"),
                        "reasoning": result.notes,
                        "team": result.team,
                        "player": result.player,
                        "game_type": game.get("game_type"),
                        "home_away": player.get("home_away"),
                    })
        return predictions
