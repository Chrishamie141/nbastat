"""Prediction grading utilities for historical betting outcomes."""

from __future__ import annotations

from typing import Any

from .markets import normalize_market


class PredictionGrader:
    """Compare frozen predictions with actual historical outcomes."""

    def grade(self, prediction: dict[str, Any], outcome: dict[str, Any] | None) -> dict[str, Any]:
        """Grade one prediction as win, loss, push, or unresolved."""
        if not outcome:
            return {"actual_result": None, "correct": None, "margin": None, "grade": "unresolved"}
        actual = outcome.get("actual_result", outcome.get("result"))
        line = prediction.get("line")
        market = normalize_market(prediction.get("market"))
        pick = str(prediction.get("prediction", "")).lower()
        if actual is None:
            return {"actual_result": None, "correct": None, "margin": None, "grade": "unresolved"}

        if market in {"total", "player_prop", "player props"} or pick in {"over", "under"}:
            if market == "total" and outcome.get("final_home_score") is not None and outcome.get("final_away_score") is not None:
                actual = float(outcome["final_home_score"]) + float(outcome["final_away_score"])
            if line is None:
                return self._binary(prediction, actual)
            margin = float(actual) - float(line)
            if margin == 0:
                return {"actual_result": actual, "correct": None, "margin": 0.0, "grade": "push"}
            correct = margin > 0 if pick != "under" else margin < 0
            return {"actual_result": actual, "correct": correct, "margin": margin, "grade": "win" if correct else "loss"}

        if market in {"spread"}:
            selection = str(prediction.get("selection") or prediction.get("prediction") or "").lower()
            home = str(outcome.get("home_team") or "").lower()
            away = str(outcome.get("away_team") or "").lower()
            if outcome.get("final_home_score") is not None and outcome.get("final_away_score") is not None:
                home_margin = float(outcome["final_home_score"]) - float(outcome["final_away_score"])
                selected_margin = -home_margin if selection in {"away", away} else home_margin
            else:
                selected_margin = float(outcome.get("margin", actual))
            margin = selected_margin + float(line or 0)
            if margin == 0:
                return {"actual_result": actual, "correct": None, "margin": margin, "grade": "push"}
            correct = margin > 0
            return {"actual_result": actual, "correct": correct, "margin": margin, "grade": "win" if correct else "loss"}

        if market == "h2h":
            home = str(outcome.get("home_team") or "").casefold()
            away = str(outcome.get("away_team") or "").casefold()
            actual_key = str(actual).casefold()
            pick_key = str(prediction.get("selection") or prediction.get("prediction") or "").casefold()
            if actual_key == "home" and home: actual_key = home
            elif actual_key == "away" and away: actual_key = away
            if pick_key == "home" and home: pick_key = home
            elif pick_key == "away" and away: pick_key = away
            correct = pick_key == actual_key
            return {"actual_result": actual, "correct": correct, "margin": None, "grade": "win" if correct else "loss"}

        return self._binary(prediction, actual)

    def _binary(self, prediction: dict[str, Any], actual: Any) -> dict[str, Any]:
        predicted = prediction.get("prediction")
        correct = str(predicted).lower() == str(actual).lower()
        return {"actual_result": actual, "correct": correct, "margin": None, "grade": "win" if correct else "loss"}


def index_outcomes(outcomes: list[dict[str, Any]]) -> dict[tuple[Any, ...], dict[str, Any]]:
    """Index outcomes by run-stable game, market, and optional player keys."""
    indexed = {}
    for outcome in outcomes:
        game = outcome.get("game") or outcome.get("game_id")
        market_results = outcome.get("market_results") if isinstance(outcome.get("market_results"), dict) else {}
        player_results = outcome.get("player_results") if isinstance(outcome.get("player_results"), dict) else {}
        if market_results or player_results:
            for market, result in market_results.items():
                canonical = normalize_market(market)
                row = {**outcome, "game": game, "market": canonical, "actual_result": result}
                indexed[(game, canonical, None)] = row
            for player, markets in player_results.items():
                for market, result in (markets or {}).items():
                    canonical = normalize_market(market)
                    row = {**outcome, "game": game, "market": canonical, "player": player, "actual_result": result}
                    indexed[(game, canonical, player)] = row
                    indexed.setdefault((game, canonical, None), row)
            continue
        canonical = normalize_market(outcome.get("market"))
        key = (game, canonical, outcome.get("player"))
        indexed[key] = outcome
        indexed.setdefault((game, canonical, None), outcome)
    return indexed
