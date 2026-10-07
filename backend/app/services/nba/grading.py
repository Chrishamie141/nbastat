"""NBA market grading with explicit push/void/pending semantics."""

from __future__ import annotations

from typing import Any


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def grade_prediction(prediction: dict, outcome: dict | None) -> dict:
    if not outcome or str(outcome.get("status") or "").upper() not in {"FINAL", "COMPLETED"}:
        return {"status": "UNRESOLVED", "actual": None, "reason": "verified_final_unavailable"}
    market = str(prediction.get("market") or "").lower()
    selection = str(prediction.get("selection") or "").strip()
    line = _number(prediction.get("line"))
    home_score, away_score = _number(outcome.get("home_score")), _number(outcome.get("away_score"))
    if market in {"moneyline", "h2h", "winner"}:
        if home_score is None or away_score is None:
            return {"status": "UNRESOLVED", "actual": None, "reason": "final_score_unavailable"}
        if home_score == away_score:
            return {"status": "PUSH", "actual": "TIE", "reason": "final_tie"}
        winner = outcome.get("home_team") if home_score > away_score else outcome.get("away_team")
        return {"status": "WIN" if selection.casefold() == str(winner).casefold() else "LOSS", "actual": winner, "reason": None}
    if market in {"spread", "game_spread"}:
        if None in {home_score, away_score, line}:
            return {"status": "UNRESOLVED", "actual": None, "reason": "score_or_line_unavailable"}
        selected_home = selection.casefold() in {"home", str(outcome.get("home_team")).casefold()}
        selected_away = selection.casefold() in {"away", str(outcome.get("away_team")).casefold()}
        if not selected_home and not selected_away:
            return {"status": "VOID", "actual": None, "reason": "unsupported_selection"}
        margin = (home_score - away_score if selected_home else away_score - home_score) + line
    elif market in {"total", "game_total"}:
        if None in {home_score, away_score, line}:
            return {"status": "UNRESOLVED", "actual": None, "reason": "score_or_line_unavailable"}
        raw = home_score + away_score - line
        margin = raw if selection.casefold() == "over" else -raw if selection.casefold() == "under" else None
        if margin is None:
            return {"status": "VOID", "actual": home_score + away_score, "reason": "unsupported_selection"}
    else:
        actuals = outcome.get("player_stats") or {}
        player = str(prediction.get("player_name") or prediction.get("player") or "")
        actual = _number((actuals.get(player) or {}).get(market))
        if actual is None:
            return {"status": "UNRESOLVED", "actual": None, "reason": "player_stat_unavailable"}
        if line is None:
            return {"status": "VOID", "actual": actual, "reason": "line_unavailable"}
        raw = actual - line
        margin = raw if selection.casefold() in {"over", "yes"} else -raw if selection.casefold() in {"under", "no"} else None
        if margin is None:
            return {"status": "VOID", "actual": actual, "reason": "unsupported_selection"}
    status = "PUSH" if abs(float(margin)) < 1e-9 else "WIN" if margin > 0 else "LOSS"
    return {"status": status, "actual": (home_score + away_score if market in {"total", "game_total"} else margin), "reason": None}
