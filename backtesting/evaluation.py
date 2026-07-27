"""Sport-agnostic helpers for paired, point-in-time model evaluations."""

from __future__ import annotations

from collections import defaultdict
from math import log, sqrt
from typing import Any, Iterable


EDGE_BOUNDS = ((float("-inf"), 0, "<0%"), (0, .02, "0-2%"), (.02, .04, "2-4%"),
               (.04, .06, "4-6%"), (.06, .08, "6-8%"), (.08, .10, "8-10%"),
               (.10, float("inf"), "10%+"))
CONFIDENCE_BOUNDS = ((0, .50, "<50%"), (.50, .55, "50-55%"), (.55, .60, "55-60%"),
                     (.60, .65, "60-65%"), (.65, .70, "65-70%"), (.70, 1.000001, "70%+"))


def american_profit(odds: float, result: str) -> float:
    """Profit for a flat one-unit stake at an executable American price."""
    price = float(odds)
    if price == 0:
        raise ValueError("American odds cannot be zero")
    if result == "push": return 0.0
    if result == "loss": return -1.0
    if result != "win": raise ValueError(f"Unsupported result: {result}")
    return price / 100 if price > 0 else 100 / abs(price)


def probability_metrics(pairs: Iterable[tuple[float, int]]) -> dict[str, Any]:
    """Score binary probabilities; callers must omit pushes/ties."""
    values = [(min(.999999, max(.000001, float(p))), int(y)) for p, y in pairs]
    if not values:
        return {"count": 0, "accuracy": None, "brier": None, "log_loss": None,
                "calibration_error": None, "calibration_buckets": []}
    buckets: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for p, y in values:
        buckets[min(9, int(p * 10))].append((p, y))
    calibration = []
    for index, rows in sorted(buckets.items()):
        mean = sum(p for p, _ in rows) / len(rows)
        actual = sum(y for _, y in rows) / len(rows)
        calibration.append({"range": f"{index*10}-{(index+1)*10}%", "count": len(rows),
                            "average_probability": mean, "actual_win_rate": actual,
                            "difference": actual - mean})
    return {"count": len(values), "accuracy": sum((p >= .5) == bool(y) for p, y in values) / len(values),
            "brier": sum((p-y)**2 for p, y in values) / len(values),
            "log_loss": -sum(y*log(p)+(1-y)*log(1-p) for p, y in values) / len(values),
            "calibration_error": sum(b["count"] * abs(b["difference"]) for b in calibration) / len(values),
            "calibration_buckets": calibration}


def betting_metrics(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Return flat-stake results using only each row's executable American odds."""
    bets = [r for r in rows if r.get("bet") and r.get("grade") in {"win", "loss", "push"}]
    profits = []
    for row in bets:
        odds = float(row["odds_used"])
        profits.append(american_profit(odds, row["grade"]))
    wins = sum(r["grade"] == "win" for r in bets); losses = sum(r["grade"] == "loss" for r in bets)
    pushes = len(bets) - wins - losses
    equity = peak = drawdown = 0.0
    streak = longest = 0
    for profit in profits:
        equity += profit; peak = max(peak, equity); drawdown = max(drawdown, peak-equity)
        streak = streak + 1 if profit < 0 else 0; longest = max(longest, streak)
    risked = float(len(bets))
    won = sum(max(0, p) for p in profits); lost = -sum(min(0, p) for p in profits)
    return {"bets": len(bets), "wins": wins, "losses": losses, "pushes": pushes,
            "win_rate": wins/(wins+losses) if wins+losses else None, "amount_risked": risked,
            "units_won": won, "units_lost": lost, "net_units": sum(profits),
            "profit_loss": sum(profits), "roi": sum(profits)/risked if risked else None,
            "cumulative_profit": profits, "max_drawdown": drawdown, "longest_losing_streak": longest}


def error_metrics(values: Iterable[tuple[float, float]]) -> dict[str, Any]:
    errors = [float(a)-float(b) for a, b in values]
    return {"count": len(errors), "mae": sum(abs(e) for e in errors)/len(errors) if errors else None,
            "rmse": sqrt(sum(e*e for e in errors)/len(errors)) if errors else None}


def edge_buckets(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped = {label: [] for _, _, label in EDGE_BOUNDS}
    for row in rows:
        edge = float(row.get("edge") or 0)
        for low, high, label in EDGE_BOUNDS:
            if low <= edge < high:
                grouped[label].append(row); break
    result = []
    for _, _, label in EDGE_BOUNDS:
        items = grouped[label]; bets = betting_metrics(items)
        graded = [r for r in items if r.get("grade") in {"win", "loss"}]
        result.append({"bucket": label, "predictions": len(items), **bets,
                       "average_edge": sum(float(r.get("edge") or 0) for r in items)/len(items) if items else None,
                       "average_predicted_probability": sum(float(r.get("model_probability") or 0) for r in items)/len(items) if items else None,
                       "actual_win_rate": sum(r["grade"] == "win" for r in graded)/len(graded) if graded else None})
    return result


def confidence_buckets(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Evaluate observed results across fixed model-probability bands."""
    values = list(rows); result = []
    for low, high, label in CONFIDENCE_BOUNDS:
        items = [r for r in values if r.get("model_probability") is not None and low <= float(r["model_probability"]) < high]
        metric = betting_metrics(items)
        graded = [r for r in items if r.get("grade") in {"win", "loss"}]
        result.append({"bucket": label, "count": len(items), **metric,
            "average_probability": sum(float(r["model_probability"]) for r in items)/len(items) if items else None,
            "observed_win_rate": sum(r["grade"] == "win" for r in graded)/len(graded) if graded else None})
    return result


def group_rows(rows: Iterable[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows: grouped[str(row.get(key, "unknown"))].append(row)
    return dict(sorted(grouped.items()))
