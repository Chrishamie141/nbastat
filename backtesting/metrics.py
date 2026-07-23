"""Backtest metric calculations."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def _accuracy(rows: list[dict[str, Any]]) -> float | None:
    graded = [r for r in rows if r.get("correct") is not None]
    if not graded:
        return None
    return sum(1 for r in graded if bool(r.get("correct"))) / len(graded)


class MetricsCalculator:
    """Calculate accuracy, ROI, units, and calibration metrics for a run."""

    def calculate(self, predictions: list[dict[str, Any]]) -> dict[str, Any]:
        """Return all supported aggregate and segment metrics."""
        graded = [p for p in predictions if p.get("correct") is not None]
        wins = sum(1 for p in graded if bool(p.get("correct")))
        losses = len(graded) - wins
        units_won = float(wins)
        units_lost = float(losses)
        roi = (units_won - units_lost) / len(graded) if graded else None
        confidences = [float(p.get("confidence") or 0) for p in predictions]
        return {
            "overall_accuracy": _accuracy(predictions),
            "weekly_accuracy": self._group_accuracy(predictions, "week"),
            "accuracy_by_confidence_bucket": self._confidence_buckets(predictions),
            "accuracy_by_market": self._group_accuracy(predictions, "market"),
            "accuracy_by_team": self._group_accuracy(predictions, "team"),
            "accuracy_by_player": self._group_accuracy(predictions, "player"),
            "accuracy_by_game_type": self._group_accuracy(predictions, "game_type"),
            "accuracy_by_home_away": self._group_accuracy(predictions, "home_away"),
            "roi": roi,
            "units_won": units_won,
            "units_lost": units_lost,
            "average_confidence": sum(confidences) / len(confidences) if confidences else None,
            "calibration": self._calibration(predictions),
            "graded_predictions": len(graded),
            "total_predictions": len(predictions),
        }

    def _group_accuracy(self, rows: list[dict[str, Any]], key: str) -> dict[str, float | None]:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[str(row.get(key) or "unknown")].append(row)
        return {group: _accuracy(items) for group, items in groups.items()}

    def _confidence_buckets(self, rows: list[dict[str, Any]]) -> dict[str, float | None]:
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            confidence = float(row.get("confidence") or 0)
            lower = int(confidence // 10) * 10
            buckets[f"{lower}-{lower + 9}"].append(row)
        return {bucket: _accuracy(items) for bucket, items in buckets.items()}

    def _calibration(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        buckets = self._confidence_buckets(rows)
        return {"bucket_accuracy": buckets, "note": "Confidence is interpreted as 0-100 probability buckets."}
