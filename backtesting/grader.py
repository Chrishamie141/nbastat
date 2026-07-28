"""Deterministic grading utilities for historical betting outcomes."""

from __future__ import annotations

import math
from typing import Any

from .markets import CANONICAL_TEAM_MARKETS, normalize_market


NFL_TEAMS = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills", "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns", "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs", "LV": "Las Vegas Raiders", "LAC": "Los Angeles Chargers",
    "LAR": "Los Angeles Rams", "MIA": "Miami Dolphins", "MIN": "Minnesota Vikings",
    "NE": "New England Patriots", "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers",
    "SEA": "Seattle Seahawks", "SF": "San Francisco 49ers", "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}
_TEAM_ALIASES = {alias.casefold(): abbr for abbr, name in NFL_TEAMS.items() for alias in (abbr, name)}
_TEAM_ALIASES.update({"jacksonville jaguars": "JAX", "washington football team": "WAS", "washington redskins": "WAS",
                      "oakland raiders": "LV", "san diego chargers": "LAC", "st. louis rams": "LAR"})


def canonical_team(value: Any) -> str:
    """Return an NFL abbreviation for known abbreviations/full names."""
    text = str(value or "").strip()
    return _TEAM_ALIASES.get(text.casefold(), text.upper())


def _ungraded(reason: str, actual: Any = None) -> dict[str, Any]:
    return {"actual_result": actual, "correct": None, "margin": None,
            "grade": "ungraded", "ungraded_reason": reason}


class PredictionGrader:
    """Compare a frozen prediction with a stored final outcome."""

    def grade(self, prediction: dict[str, Any], outcome: dict[str, Any] | None) -> dict[str, Any]:
        if not outcome:
            return _ungraded("missing_outcome")
        market = normalize_market(prediction.get("market"))
        selection = prediction.get("selection", prediction.get("prediction"))
        pick = str(selection or "").strip().casefold()
        actual = outcome.get("actual_result", outcome.get("result"))

        if market in CANONICAL_TEAM_MARKETS:
            scores = self._scores(outcome)
            home, away = canonical_team(outcome.get("home_team")), canonical_team(outcome.get("away_team"))

            if market == "h2h":
                if scores is None and actual is not None:
                    expected = str(actual).strip().casefold()
                    correct = pick == expected
                    return self._result("win" if correct else "loss", actual)
                if scores is None:
                    return _ungraded("missing_final_score", actual)
                home_score, away_score = scores
                selected = home if pick == "home" else away if pick == "away" else canonical_team(selection)
                if not selected or selected not in {home, away}:
                    return _ungraded("unsupported_selection", actual)
                if home_score == away_score:
                    return self._result("push", "tie", 0.0)
                winner = home if home_score > away_score else away
                return self._result("win" if selected == winner else "loss", winner)

            if scores is None:
                return _ungraded("missing_final_score", actual)
            home_score, away_score = scores

            if market == "spread":
                line = self._line(prediction)
                if isinstance(line, dict):
                    return line
                selected_home = pick == "home" or canonical_team(selection) == home
                selected_away = pick == "away" or canonical_team(selection) == away
                if not selected_home and not selected_away:
                    return _ungraded("unsupported_selection", actual)
                selected_score, opponent_score = (home_score, away_score) if selected_home else (away_score, home_score)
                margin = selected_score + line - opponent_score
                return self._margin_result(margin, selected_score - opponent_score)

            if pick not in {"over", "under"}:
                return _ungraded("unsupported_selection", actual)
            line = self._line(prediction)
            if isinstance(line, dict):
                return line
            total = home_score + away_score
            margin = total - line
            if pick == "under":
                margin = -margin
            return self._margin_result(margin, total)

        if actual is None:
            return _ungraded("missing_outcome")
        if market in {"player_prop", "player props"} or pick in {"over", "under"}:
            line = self._line(prediction)
            if isinstance(line, dict):
                return line
            try:
                margin = float(actual) - line
            except (TypeError, ValueError):
                return _ungraded("invalid_line", actual)
            if pick == "under":
                margin = -margin
            return self._margin_result(margin, actual)
        correct = pick == str(actual).strip().casefold()
        return self._result("win" if correct else "loss", actual)

    @staticmethod
    def _scores(outcome: dict[str, Any]) -> tuple[float, float] | None:
        try:
            home, away = float(outcome["final_home_score"]), float(outcome["final_away_score"])
            return (home, away) if math.isfinite(home) and math.isfinite(away) else None
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _line(prediction: dict[str, Any]) -> float | dict[str, Any]:
        if prediction.get("line") in (None, ""):
            return _ungraded("missing_line")
        try:
            line = float(prediction["line"])
            return line if math.isfinite(line) else _ungraded("invalid_line")
        except (TypeError, ValueError):
            return _ungraded("invalid_line")

    @classmethod
    def _margin_result(cls, margin: float, actual: Any) -> dict[str, Any]:
        return cls._result("push" if margin == 0 else "win" if margin > 0 else "loss", actual, margin)

    @staticmethod
    def _result(grade: str, actual: Any, margin: float | None = None) -> dict[str, Any]:
        return {"actual_result": actual, "correct": True if grade == "win" else False if grade == "loss" else None,
                "margin": margin, "grade": grade, "ungraded_reason": None}


def index_outcomes(outcomes: list[dict[str, Any]]) -> dict[tuple[Any, ...], dict[str, Any]]:
    """Index outcomes, deriving every team market directly from final scores."""
    indexed: dict[tuple[Any, ...], dict[str, Any]] = {}
    for outcome in outcomes:
        # ``game_id`` is the canonical identity assigned by normalize_outcomes.
        # Real provider finals may retain a provider-specific ``game`` field;
        # preferring it here silently bypasses reconciliation.
        game = outcome.get("game_id") or outcome.get("game") or outcome.get("id")
        context = (str(outcome.get("league") or "").lower(), str(outcome.get("season") or ""), int(outcome.get("week") or 0))
        market_results = outcome.get("market_results") if isinstance(outcome.get("market_results"), dict) else {}
        for market, result in market_results.items():
            canonical = normalize_market(market)
            indexed[(game, canonical, None)] = {**outcome, "game": game, "market": canonical, "actual_result": result}
            indexed[(*context, game, canonical, None)] = indexed[(game, canonical, None)]
        # A final score is sufficient to grade all three team markets.  Do not
        # require redundant market_results entries in historical snapshots.
        if outcome.get("final_home_score") is not None and outcome.get("final_away_score") is not None:
            for market in CANONICAL_TEAM_MARKETS:
                indexed.setdefault((game, market, None), {**outcome, "game": game, "market": market})
                indexed.setdefault((*context, game, market, None), indexed[(game, market, None)])
        for player, markets in (outcome.get("player_results") or {}).items():
            for market, result in (markets or {}).items():
                canonical = normalize_market(market)
                row = {**outcome, "game": game, "market": canonical, "player": player, "actual_result": result}
                indexed[(game, canonical, player)] = row
                indexed[(*context, game, canonical, player)] = row
                indexed.setdefault((game, canonical, None), row)
        canonical = normalize_market(outcome.get("market"))
        if canonical:
            indexed[(game, canonical, outcome.get("player"))] = outcome
            indexed[(*context, game, canonical, outcome.get("player"))] = outcome
    return indexed


def lookup_outcome(index: dict[tuple[Any, ...], dict[str, Any]], prediction: dict[str, Any], league: str, season: str, week: int) -> dict[str, Any] | None:
    """Look up a final with season/week isolation and a legacy-index fallback."""
    game = prediction.get("game_id") or prediction.get("game")
    market = normalize_market(prediction.get("market"))
    player = prediction.get("player")
    contextual = (str(league).lower(), str(season), int(week), game, market, player)
    result = index.get(contextual)
    if result is not None:
        return result
    # Old callers may index context-free fixtures.  Never use that fallback
    # once a contextual index is present: doing so would cross week boundaries.
    if not any(len(key) == 6 for key in index):
        return index.get((game, market, player))
    return None
