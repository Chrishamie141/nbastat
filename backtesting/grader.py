"""Prediction grading utilities for historical betting outcomes."""

from __future__ import annotations

from typing import Any


class PredictionGrader:
    """Compare frozen predictions with actual historical outcomes."""

    def grade(self, prediction: dict[str, Any], outcome: dict[str, Any] | None) -> dict[str, Any]:
        """Grade one prediction as win, loss, push, or unresolved."""
        if not outcome:
            return {"actual_result": None, "correct": None, "margin": None, "grade": "unresolved"}
        actual = outcome.get("actual_result", outcome.get("result"))
        line = prediction.get("line")
        market = str(prediction.get("market", "")).lower()
        pick = str(prediction.get("prediction", "")).lower()
        if actual is None:
            return {"actual_result": None, "correct": None, "margin": None, "grade": "unresolved"}

        if market in {"over_under", "total", "player_prop", "player props"} or pick in {"over", "under"}:
            if line is None:
                return self._binary(prediction, actual)
            margin = float(actual) - float(line)
            if margin == 0:
                return {"actual_result": actual, "correct": None, "margin": 0.0, "grade": "push"}
            correct = margin > 0 if pick != "under" else margin < 0
            return {"actual_result": actual, "correct": correct, "margin": margin, "grade": "win" if correct else "loss"}

        if market in {"spread"}:
            margin = float(outcome.get("margin", actual)) - float(line or 0)
            if margin == 0:
                return {"actual_result": actual, "correct": None, "margin": margin, "grade": "push"}
            correct = margin > 0
            return {"actual_result": actual, "correct": correct, "margin": margin, "grade": "win" if correct else "loss"}

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
                row = {"game": game, "market": market, "actual_result": result}
                indexed[(game, market, None)] = row
            for player, markets in player_results.items():
                for market, result in (markets or {}).items():
                    row = {"game": game, "market": market, "player": player, "actual_result": result}
                    indexed[(game, market, player)] = row
                    indexed.setdefault((game, market, None), row)
            continue
        key = (game, outcome.get("market"), outcome.get("player"))
        indexed[key] = outcome
        indexed.setdefault((game, outcome.get("market"), None), outcome)
    return indexed
