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
from .nfl_game_predictor import NFLGameMarketPredictor
from .nfl_game_predictor import no_vig_probabilities, sportsbook_consensus
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
            games_by_id = {(game.get("game_id") or game.get("id") or game.get("game")): game for game in games}
            outcomes_raw = [
                {**games_by_id.get(outcome.get("game_id") or outcome.get("game") or outcome.get("id"), {}), **outcome}
                for outcome in outcomes_raw
            ]
            outcomes = index_outcomes(outcomes_raw)
            graded_count = 0
            grades: Counter[str] = Counter()
            ungraded_reasons: Counter[str] = Counter()
            for prediction_id, prediction in frozen:
                key = (prediction.get("game"), normalize_market(prediction.get("market")), prediction.get("player"))
                grade = self.grader.grade(prediction, outcomes.get(key))
                grades[grade["grade"]] += 1
                if grade["grade"] in {"win", "loss", "push"}:
                    graded_count += 1
                else:
                    ungraded_reasons[grade.get("ungraded_reason") or "other"] += 1
                self.store.grade_prediction(prediction_id, grade)
                if os.getenv("BACKTESTING_DEBUG_PREDICTIONS") == "1":
                    outcome = outcomes.get(key) or {}
                    detail = {
                        "game_id": prediction.get("game"), "market": normalize_market(prediction.get("market")),
                        "selection": prediction.get("selection", prediction.get("prediction")), "line": prediction.get("line"),
                        "odds": prediction.get("sportsbook_odds", prediction.get("odds")), "sportsbook": prediction.get("sportsbook"),
                        "home_team": outcome.get("home_team"), "away_team": outcome.get("away_team"),
                        "final_home_score": outcome.get("final_home_score"), "final_away_score": outcome.get("final_away_score"),
                        "grade": grade["grade"], "ungraded_reason": grade.get("ungraded_reason"),
                    }
                    print(f"  Grade: {json.dumps(detail, sort_keys=True, default=str)}")
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
            coverage = evaluation.get("history_coverage", {})
            print(f"- History coverage: teams={coverage.get('teams', 0)}, rows_loaded={coverage.get('rows_loaded', 0)}, rows_used={coverage.get('rows_used', 0)}, rows_rejected={coverage.get('rows_rejected', 0)}, minimum_history_failures={coverage.get('minimum_history_failures', 0)}")
            if not coverage.get("rows_used") and coverage.get("dominant_rejection_reason"):
                print(f"- Dominant history rejection: {coverage['dominant_rejection_reason']}")
            if os.getenv("BACKTESTING_DEBUG_PREDICTIONS") == "1":
                print("- Prediction diagnostics:")
                for game_diagnostic in evaluation.get("games", []):
                    print(f"  {json.dumps(game_diagnostic, sort_keys=True, default=str)}")
            print(f"- Outcomes loaded: {len(outcomes_raw)}")
            print(f"- Predictions graded: {graded_count}")
            print(f"- Accepted bets: {len(frozen)}")
            print(f"- Graded: {graded_count}")
            print(f"- Wins: {grades['win']}")
            print(f"- Losses: {grades['loss']}")
            print(f"- Pushes: {grades['push']}")
            print(f"- Ungraded: {grades['ungraded']}")
            if ungraded_reasons:
                print("- Ungraded reasons: " + ", ".join(f"{reason}={count}" for reason, count in sorted(ungraded_reasons.items())))
        stored = self.store.load_predictions(self.metadata.run_id)
        metrics = self.metrics.calculate(stored)
        report_dir = None
        if self.config.export:
            report_dir = ReportExporter(self.config.results_dir).export(self.metadata, stored, metrics)
        evaluation = self._aggregate_evaluation()
        summary = {"run_id": self.metadata.run_id, "mode": self.config.mode().value, "metrics": metrics, "evaluation": evaluation, "report_dir": str(report_dir) if report_dir else None}
        print(f"Final report: run_id={summary['run_id']} mode={summary['mode']} predictions={metrics.get('total_predictions', 0)}")
        print(f"Accepted bets: {metrics['total_predictions']}")
        print(f"Graded: {metrics['graded_predictions']}")
        print(f"Wins: {metrics['wins']}")
        print(f"Losses: {metrics['losses']}")
        print(f"Pushes: {metrics['pushes']}")
        print(f"Ungraded: {metrics['ungraded_predictions']}")
        run_ungraded = Counter(row.get("ungraded_reason") or "other" for row in stored if row.get("grade") == "ungraded")
        if run_ungraded:
            print("Ungraded reasons: " + ", ".join(f"{reason}={count}" for reason, count in sorted(run_ungraded.items())))
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
        game_predictor = NFLGameMarketPredictor(config.model_version)
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
                    team_predictions, team_decisions, team_reasons = self._evaluate_team_market(
                        game_predictor, game, market, game_odds, team_stats, config
                    )
                    candidates_evaluated += len(team_decisions) - sum(1 for row in team_decisions if row.get("model_probability") is None)
                    predictions.extend(team_predictions)
                    game_decisions.extend(team_decisions)
                    game_reasons.update(team_reasons)
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
                "history": game_predictor.last_diagnostics,
            })
        # NFLPredictor's team-market method is explicitly placeholder-only (zero projection), so replay must not
        # turn complete h2h/spread/total prices into fabricated bets.
        history_diags = [team for game_diag in diagnostics for team in game_diag.get("history", {}).values()]
        self._last_prediction_diagnostics = {
            "games_evaluated": len(games), "markets_evaluated": markets_evaluated,
            "candidates_evaluated": candidates_evaluated, "bets_accepted": len(predictions),
            "no_bet_reasons": dict(sorted(reasons.items())), "games": diagnostics,
            "supported_prediction_markets": sorted(supported_player_markets),
            "history_coverage": self._history_coverage(history_diags),
        }
        return predictions

    @staticmethod
    def _history_coverage(history_diags):
        # A team is diagnosed once per market; report unique-row-equivalent totals
        # by taking each team's maximum counters across repeated projections.
        unique = {}
        for row in history_diags:
            key = row.get("team") or (tuple(row.get("seasons_used", [])), row.get("latest_history_timestamp"), row.get("history_rows_available"))
            unique[key] = row
        rows = list(unique.values())
        rejections = Counter()
        for row in rows: rejections.update(row.get("rejection_reasons", {}))
        loaded = sum(int(row.get("history_rows_loaded", 0)) for row in rows)
        used = sum(int(row.get("history_rows_used", 0)) for row in rows)
        return {"teams": len(rows), "rows_loaded": loaded, "rows_used": used,
            "rows_rejected": sum(rejections.values()),
            "minimum_history_failures": sum(int(row.get("history_rows_used", 0)) < int(row.get("minimum_required", 0)) for row in rows),
            "rejected_future_rows": sum(int(row.get("rejected_future_rows", 0)) for row in rows),
            "rejection_reasons": dict(sorted(rejections.items())),
            "dominant_rejection_reason": max(rejections, key=rejections.get) if rejections else None}

    def _evaluate_team_market(self, predictor, game, market, game_odds, team_stats, config):
        """Create model evaluations first, then apply betting acceptance thresholds."""
        rows = [row for row in game_odds if normalize_market(row.get("market")) == market]
        kickoff = game.get("kickoff_time") or game.get("commence_time")
        valid = []
        for row in rows:
            implied = self._implied_probability(row.get("odds"))
            captured = row.get("captured_at") or row.get("snapshot_timestamp")
            if implied is None or not row.get("selection") or (market != "h2h" and row.get("line") is None) or not self._timestamp_before(captured, kickoff):
                continue
            valid.append(row)
        if not valid:
            return [], [{"market": market, "decision": "rejected", "rejection_reason": "invalid_or_missing_odds", "reason": "invalid_or_missing_odds", "model_probability": None}], Counter({"invalid_or_missing_odds": 1})
        projection = predictor.project(game, team_stats)
        if projection is None:
            return [], [{"market": market, "decision": "rejected", "rejection_reason": "insufficient_pregame_history", "reason": "insufficient_pregame_history", "model_probability": None}], Counter({"insufficient_pregame_history": 1})
        # Consensus is context; the best separately retained quote is executable.
        consensus = sportsbook_consensus(valid, market)
        consensus_probabilities: dict[str, list[float]] = {}
        groups: dict[tuple[str, Any], list[dict[str, Any]]] = {}
        for quote in valid:
            groups.setdefault((str(quote.get("sportsbook") or ""), quote.get("line") if market != "h2h" else None), []).append(quote)
        for quotes in groups.values():
            selections = {str(q["selection"]).casefold() for q in quotes}
            if len(selections) != 2:
                continue
            prices = [float(q["odds"]) for q in quotes]
            for quote, probability in zip(quotes, no_vig_probabilities(prices)):
                consensus_probabilities.setdefault(str(quote["selection"]).casefold(), []).append(probability)
        market_probability = {selection: __import__("statistics").median(values) for selection, values in consensus_probabilities.items()}
        # A projection is computed once above. Quotes merely price its distribution.
        by_selection = {}
        for row in valid:
            selection = str(row["selection"])
            try:
                probability = projection.probability(market, selection, row.get("line"), home_team=str(game.get("home_team")), away_team=str(game.get("away_team")))
            except (TypeError, ValueError):
                continue
            implied = self._implied_probability(row.get("odds"))
            consensus_implied = market_probability.get(selection.casefold(), implied)
            evaluated = {**row, "model_probability": probability, "implied_probability": consensus_implied,
                         "execution_implied_probability": implied, "edge": probability - consensus_implied,
                         "consensus_line": consensus.get("consensus_line")}
            key = selection.casefold()
            current = by_selection.get(key)
            rank = (evaluated["edge"], float(evaluated["odds"]), str(evaluated.get("sportsbook") or ""))
            if current is None or rank > current[0]:
                by_selection[key] = (rank, evaluated)
        if not by_selection:
            return [], [{"market": market, "decision": "rejected", "rejection_reason": "model_evaluation_failed", "reason": "model_evaluation_failed", "model_probability": None}], Counter({"model_evaluation_failed": 1})
        accepted, decisions, reasons = [], [], Counter()
        for _, row in sorted(by_selection.values(), key=lambda item: (str(item[1]["selection"]).casefold(), str(item[1].get("sportsbook") or ""))):
            reason = None
            if row["edge"] < 0.02:
                reason = "edge_below_threshold"
            elif projection.confidence < 55.0:
                reason = "confidence_below_threshold"
            decision = "accepted" if reason is None else "rejected"
            if reason is not None:
                reasons[reason] += 1
            diagnostic = {"market": market, "selection": row["selection"], "model_probability": row["model_probability"], "implied_probability": row["implied_probability"], "execution_implied_probability": row["execution_implied_probability"], "edge": row["edge"], "confidence": projection.confidence, "sportsbook": row.get("sportsbook"), "line": row.get("line"), "consensus_line": row.get("consensus_line"), "american_odds": row.get("odds"), "decision": decision, "rejection_reason": reason, "reason": reason, "threshold": 0.02, "model_version": projection.model_version, "features_data_as_of": projection.data_as_of}
            decisions.append(diagnostic)
            if reason is not None:
                continue
            selection = str(row["selection"])
            accepted.append({"game": game.get("game_id") or game.get("id") or game.get("game"), "prediction": selection, "selection": selection, "market": market, "line": row.get("line"), "sportsbook_odds": row.get("odds"), "american_odds": row.get("odds"), "sportsbook": row.get("sportsbook"), "model_probability": row["model_probability"], "implied_probability": row["implied_probability"], "edge": row["edge"], "confidence": projection.confidence, "prediction_model_version": projection.model_version, "features_data_as_of": projection.data_as_of, "features": projection.features, "reasoning": f"{projection.model_version}: prior scoring offense/defense plus home-field adjustment", "team": selection if market != "total" else None, "home_away": "home" if selection.casefold() in {"home", str(game.get("home_team")).casefold()} else "away" if market != "total" else None, "game_type": game.get("game_type"), "clv": row.get("closing_line_value") or row.get("clv")})
        return accepted, decisions, reasons

    @staticmethod
    def _timestamp_before(value, kickoff):
        if not value or not kickoff:
            return False
        try:
            from datetime import datetime, timezone
            observed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            start = datetime.fromisoformat(str(kickoff).replace("Z", "+00:00"))
            if observed.tzinfo is None: observed = observed.replace(tzinfo=timezone.utc)
            if start.tzinfo is None: start = start.replace(tzinfo=timezone.utc)
            return observed < start
        except (TypeError, ValueError):
            return False

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
        evaluation["candidates_generated"] = totals["candidates_evaluated"]
        evaluation["bets_rejected"] = totals["candidates_evaluated"] - totals["bets_accepted"]
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
