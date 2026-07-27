"""Chronological replay engine for isolated internal backtesting."""

from __future__ import annotations

from collections import Counter
import json
import os
from typing import Any, Callable

from nfl_predictor import NFLPredictor

from .config import BacktestConfig, PredictionMode
from .grader import PredictionGrader, index_outcomes
from .metrics import MetricsCalculator
from .markets import CANONICAL_TEAM_MARKETS, normalize_market
from .historical_provider import HistoricalSnapshotProvider, PredictionDataProvider
from .prediction_store import PredictionStore
from .reports import ReportExporter
from .snapshots import SnapshotError
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
        self._last_prediction_diagnostics: dict[str, Any] = {}
        self._evaluation_weeks: dict[str, dict[str, Any]] = {}

    def run(self) -> dict[str, Any]:
        """Execute the replay, grade predictions, export reports, and return a summary."""
        self.store.create_run(self.metadata)
        self._evaluation_weeks = {}
        for week in self._weeks():
            self._last_prediction_diagnostics = {}
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
            evaluation = self._last_prediction_diagnostics or self._fallback_evaluation(games, odds, predictions)
            self._evaluation_weeks[str(week)] = evaluation
            frozen: list[tuple[int, dict[str, Any]]] = []
            for prediction in predictions:
                prediction.setdefault("generated_timestamp", utc_now_iso())
                frozen.append((self.store.save_prediction(self.metadata, week, prediction), prediction.copy()))
            outcomes_raw = getattr(self.provider, "get_outcomes")(self.config.league, self.config.season, week)
            outcomes = index_outcomes(outcomes_raw)
            graded_count = 0
            for prediction_id, prediction in frozen:
                key = (prediction.get("game"), normalize_market(prediction.get("market")), prediction.get("player"))
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
            print(f"- Games evaluated: {evaluation['games_evaluated']}")
            print(f"- Markets evaluated: {evaluation['markets_evaluated']}")
            print(f"- Candidates evaluated: {evaluation['candidates_evaluated']}")
            print(f"- Bets accepted: {evaluation['bets_accepted']}")
            reasons = evaluation.get("no_bet_reasons", {})
            print(f"- No-bet reasons: {', '.join(f'{key}={value}' for key, value in sorted(reasons.items())) if reasons else 'none'}")
            if os.getenv("BACKTESTING_DEBUG_PREDICTIONS") == "1":
                print("- Prediction diagnostics:")
                for game_diagnostic in evaluation.get("games", []):
                    print(f"  {json.dumps(game_diagnostic, sort_keys=True, default=str)}")
            print(f"- Outcomes loaded: {len(outcomes_raw)}")
            print(f"- Predictions graded: {graded_count}")
        stored = self.store.load_predictions(self.metadata.run_id)
        metrics = self.metrics.calculate(stored)
        report_dir = None
        if self.config.export:
            report_dir = ReportExporter(self.config.results_dir).export(self.metadata, stored, metrics)
        evaluation = self._aggregate_evaluation()
        summary = {"run_id": self.metadata.run_id, "mode": self.config.mode().value, "metrics": metrics, "evaluation": evaluation, "report_dir": str(report_dir) if report_dir else None}
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
            self._last_prediction_diagnostics = {"games_evaluated": 0, "markets_evaluated": 0, "candidates_evaluated": 0, "bets_accepted": 0, "no_bet_reasons": {"unsupported_league": 1}, "games": []}
            return []
        predictor = NFLPredictor()
        predictions: list[dict[str, Any]] = []
        games = provider.get_games(config.league, config.season, week)
        odds = provider.get_odds(config.league, config.season, week)
        player_stats = provider.get_player_stats(config.league, config.season, week)
        # The production adapter currently uses team history only to enrich
        # diagnostics.  Missing optional history must not invalidate an
        # otherwise complete player-prop replay.  A model that actually needs
        # it should call provider.get_team_stats() itself, which remains strict.
        team_stats = self._optional_team_stats(provider, config, week)
        requested = set(config.normalized_markets())
        supported_player_markets = {"PASS_YDS", "RUSH_YDS", "REC_YDS", "RECEPTIONS", "TD", "PASS_TD", "PASS_INT"}
        odds_by_game = {}
        for odd in odds:
            odds_by_game.setdefault(odd.get("game_id"), []).append(odd)
        diagnostics: list[dict[str, Any]] = []
        reasons: Counter[str] = Counter()
        markets_evaluated = candidates_evaluated = 0
        for game in games:
            candidate_before = candidates_evaluated
            game_decisions: list[dict[str, Any]] = []
            game_id = game.get("game_id") or game.get("id") or game.get("game")
            game_odds = odds_by_game.get(game_id, [])
            available_markets = sorted({normalize_market(row.get("market")) for row in game_odds if row.get("market")})
            selected_markets = [market for market in available_markets if not requested or market in requested]
            game_reasons: Counter[str] = Counter()
            if requested and not selected_markets:
                game_reasons["market_filter_mismatch"] += 1
            for market in selected_markets:
                markets_evaluated += 1
                if market in CANONICAL_TEAM_MARKETS:
                    game_reasons["unsupported_market"] += 1
                    game_decisions.append({"market": market, "model_probability": None, "implied_probability": None, "edge": None, "confidence": None, "threshold": None, "decision": "no_bet", "reason": "unsupported_market"})
            if game_id not in odds_by_game:
                game_reasons["odds_lookup_failed"] += 1
            players = game.get("players") or []
            if not players:
                teams = {game.get("home_team"), game.get("away_team")}
                players = [p for p in player_stats if p.get("team") in teams]
            for player in players:
                for result in predictor.predict_player(player):
                    stat_type = str(result.stat_type)
                    if requested and normalize_market(stat_type) not in requested:
                        continue
                    matching_odds = [o for o in game_odds if normalize_market(o.get("market")) == normalize_market(stat_type) and (not o.get("selection") or o.get("selection") == result.player)]
                    line = (matching_odds[0].get("line") if matching_odds else player.get("line"))
                    pick = "over" if line is not None and float(result.prediction) > float(line) else result.prediction
                    if config.mode() is PredictionMode.BETTING and not matching_odds:
                        game_reasons["odds_lookup_failed"] += 1
                        continue
                    candidates_evaluated += 1
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
                    game_decisions.append({"market": normalize_market(stat_type), "selection": result.player, "model_probability": None, "implied_probability": self._implied_probability(odds_row.get("odds")), "edge": edge, "confidence": result.confidence, "threshold": None, "decision": "accepted", "reason": None})
            reasons.update(game_reasons)
            team_names = {game.get("home_team"), game.get("away_team")}
            game_players = [p for p in player_stats if p.get("team") in team_names]
            game_teams = [t for t in team_stats if t.get("team") in team_names]
            diagnostics.append({
                "game_id": game_id, "home_team": game.get("home_team"), "away_team": game.get("away_team"),
                "kickoff": game.get("kickoff_time") or game.get("commence_time"), "odds_rows_available": len(game_odds),
                "markets_available": available_markets, "player_stats_available": len(game_players), "team_stats_available": len(game_teams),
                "prediction_candidates_created": candidates_evaluated - candidate_before,
                "candidates_accepted": sum(1 for p in predictions if p.get("game") == game_id),
                "candidates_rejected": sum(game_reasons.values()), "rejection_reasons": dict(sorted(game_reasons.items())),
                "market_decisions": game_decisions,
            })
        # NFLPredictor's team-market method is explicitly placeholder-only (zero projection), so replay must not
        # turn complete h2h/spread/total prices into fabricated bets.
        self._last_prediction_diagnostics = {
            "games_evaluated": len(games), "markets_evaluated": markets_evaluated,
            "candidates_evaluated": candidates_evaluated, "bets_accepted": len(predictions),
            "no_bet_reasons": dict(sorted(reasons.items())), "games": diagnostics,
            "supported_prediction_markets": sorted(supported_player_markets),
        }
        return predictions

    def _fallback_evaluation(self, games: list[dict[str, Any]], odds: list[dict[str, Any]], predictions: list[dict[str, Any]]) -> dict[str, Any]:
        """Supply concise accounting for injected factories that predate diagnostics."""
        reasons = {}
        if not odds:
            reasons["missing_historical_odds"] = len(games) or 1
        elif not predictions:
            reasons["no_predictions_generated"] = len(games) or 1
        return {"games_evaluated": len(games), "markets_evaluated": len({normalize_market(o.get("market")) for o in odds if o.get("market")}), "candidates_evaluated": len(predictions), "bets_accepted": len(predictions), "no_bet_reasons": reasons, "games": []}

    @staticmethod
    def _optional_team_stats(provider: PredictionDataProvider, config: BacktestConfig, week: int) -> list[dict[str, Any]]:
        """Load diagnostic-only team history, degrading an absent dataset to empty."""
        try:
            return provider.get_team_stats(config.league, config.season, week)
        except AttributeError:
            return []
        except SnapshotError as exc:
            if str(exc).startswith("No team_stats snapshot found"):
                return []
            raise

    def _aggregate_evaluation(self) -> dict[str, Any]:
        """Return complete per-week and run-level evaluation accounting."""
        fields = ("games_evaluated", "markets_evaluated", "candidates_evaluated", "bets_accepted")
        totals = {field: sum(int(record.get(field, 0)) for record in self._evaluation_weeks.values()) for field in fields}
        reasons: Counter[str] = Counter()
        for record in self._evaluation_weeks.values():
            reasons.update(record.get("no_bet_reasons", {}))
        evaluation: dict[str, Any] = {
            "totals": totals,
            "no_bet_reasons": dict(sorted(reasons.items())),
            "weeks": dict(self._evaluation_weeks),
        }
        # Retain the original single-run fields for report consumers while the
        # explicit totals/weeks structure supplies unambiguous aggregation.
        evaluation.update(totals)
        if len(self._evaluation_weeks) == 1:
            only_week = next(iter(self._evaluation_weeks.values()))
            evaluation["games"] = only_week.get("games", [])
            if "supported_prediction_markets" in only_week:
                evaluation["supported_prediction_markets"] = only_week["supported_prediction_markets"]
        else:
            evaluation["games"] = [game for record in self._evaluation_weeks.values() for game in record.get("games", [])]
        return evaluation

    @staticmethod
    def _implied_probability(american_odds: Any) -> float | None:
        try:
            price = float(american_odds)
        except (TypeError, ValueError):
            return None
        if price == 0:
            return None
        return (-price / (-price + 100)) if price < 0 else (100 / (price + 100))
