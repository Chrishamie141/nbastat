"""Chronological, leakage-safe NBA replay evaluation."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import math
from typing import Callable

from .grading import grade_prediction
from .season import normalize_nba_season


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class NBAReplayEngine:
    """Evaluate a frozen NBA model in strict event-time order."""

    def __init__(self, *, season: str | int, model_version: str, prediction_factory: Callable):
        self.season = normalize_nba_season(season).code
        if not str(model_version).startswith("nba-"):
            raise ValueError("NBA replay requires an NBA-owned model version")
        self.model_version = model_version
        self.prediction_factory = prediction_factory

    @staticmethod
    def _valid_market(row: dict, prediction: dict, kickoff: datetime, generated: datetime) -> bool:
        try:
            observed = _dt(row["observed_at"])
            odds = int(row["odds"])
        except (KeyError, TypeError, ValueError):
            return False
        same_line = (row.get("line") is None and prediction.get("line") is None) or row.get("line") == prediction.get("line")
        return bool(
            row.get("game_id") == prediction.get("game_id")
            and str(row.get("market") or "").lower() == str(prediction.get("market") or "").lower()
            and str(row.get("selection") or "").casefold() == str(prediction.get("selection") or "").casefold()
            and same_line and row.get("provider") and row.get("sportsbook") and odds != 0
            and row.get("validation_status", "VALID_PREGAME") == "VALID_PREGAME"
            and observed < kickoff and observed <= generated
        )

    @staticmethod
    def _profit(price: int) -> float:
        return price / 100 if price > 0 else 100 / abs(price)

    def run(self, *, games: list[dict], history: list[dict], outcomes: list[dict],
            markets: list[dict] | None = None) -> dict:
        outcome_index = {str(row["game_id"]): row for row in outcomes}
        markets = markets or []
        rows = []
        for game in sorted(games, key=lambda row: (_dt(row["kickoff_time"]), str(row["game_id"]))):
            kickoff = _dt(game["kickoff_time"])
            eligible_history = [dict(row) for row in history if row.get("completed_at") and _dt(row["completed_at"]) < kickoff]
            predictions = self.prediction_factory(dict(game), eligible_history, self.model_version) or []
            for prediction in predictions:
                generated = _dt(prediction.get("generated_at") or game["kickoff_time"])
                if generated >= kickoff:
                    raise ValueError(f"prediction_at_or_after_kickoff:{game['game_id']}")
                frozen = {**prediction, "game_id": str(game["game_id"]), "season": self.season,
                          "kickoff_time": game["kickoff_time"], "model_version": self.model_version}
                result = grade_prediction(frozen, outcome_index.get(str(game["game_id"])))
                eligible_markets = [row for row in markets if self._valid_market(row, frozen, kickoff, generated)]
                entry = max(eligible_markets, key=lambda row: _dt(row["observed_at"]), default=None)
                rows.append({**frozen, "grade": result["status"], "actual": result["actual"],
                             "ungraded_reason": result["reason"], "history_rows": len(eligible_history),
                             "marketObservation": None if not entry else {
                                 "provider": entry["provider"], "sportsbook": entry["sportsbook"],
                                 "odds": int(entry["odds"]), "observedAt": entry["observed_at"],
                             }})
        decisions = [row for row in rows if row["grade"] in {"WIN", "LOSS"}]
        wins = sum(row["grade"] == "WIN" for row in decisions)
        probabilities = [(max(.000001, min(.999999, float(row["model_probability"]))), 1 if row["grade"] == "WIN" else 0)
                         for row in decisions if row.get("model_probability") is not None]
        buckets = []
        for label, low, high in (("50-54.99%", .50, .55), ("55-59.99%", .55, .60),
                                 ("60-64.99%", .60, .65), ("65%+", .65, 1.01)):
            subset = [(p, y) for p, y in probabilities if low <= p < high]
            buckets.append({"range": label, "predictions": len(subset),
                            "averageProbability": round(sum(p for p, _ in subset) / len(subset), 4) if subset else None,
                            "actualWinRate": round(sum(y for _, y in subset) / len(subset), 4) if subset else None,
                            "sampleStatus": "REPORTABLE" if len(subset) >= 30 else "INSUFFICIENT_SAMPLE"})
        priced = [row for row in rows if row["grade"] in {"WIN", "LOSS", "PUSH"} and row["marketObservation"]]
        units = sum(self._profit(row["marketObservation"]["odds"]) if row["grade"] == "WIN"
                    else -1 if row["grade"] == "LOSS" else 0 for row in priced)
        by_market = defaultdict(lambda: {"predictions": 0, "wins": 0, "losses": 0, "pushes": 0, "unresolved": 0})
        by_period = defaultdict(lambda: {"predictions": 0, "wins": 0, "losses": 0, "pushes": 0, "unresolved": 0})
        for row in rows:
            period = _dt(row["kickoff_time"]).strftime("%Y-%m")
            for target in (by_market[str(row.get("market") or "unknown")], by_period[period]):
                target["predictions"] += 1
                key = "wins" if row["grade"] == "WIN" else "losses" if row["grade"] == "LOSS" else "pushes" if row["grade"] == "PUSH" else "unresolved"
                target[key] += 1
        metrics = {"gamesEvaluated": len({row["game_id"] for row in rows}),
                   "predictionsEvaluated": len(rows), "total": len(rows), "graded": len(decisions),
                   "wins": wins, "losses": len(decisions) - wins,
                   "pushes": sum(row["grade"] == "PUSH" for row in rows),
                   "unresolved": sum(row["grade"] == "UNRESOLVED" for row in rows),
                   "pending": sum(row["grade"] == "UNRESOLVED" for row in rows),
                   "accuracy": wins / len(decisions) if decisions else None,
                   "brierScore": (sum((p-y) ** 2 for p, y in probabilities) / len(probabilities) if probabilities else None),
                   "logLoss": (-sum(y*math.log(p)+(1-y)*math.log(1-p) for p, y in probabilities) / len(probabilities) if probabilities else None),
                   "calibrationBuckets": buckets, "marketCoverage": len(priced) / len(rows) if rows else None,
                   "validWagerCount": len(priced), "units": round(units, 4) if priced else None,
                   "roi": round(units / len(priced) * 100, 2) if priced else None,
                   "byMarket": dict(by_market), "byModelVersion": {self.model_version: {"predictions": len(rows), "wins": wins, "losses": len(decisions)-wins}},
                   "byTimePeriod": dict(by_period),
                   "missingData": {"probability": len(decisions)-len(probabilities),
                                   "validPregameMarket": len(rows)-len(priced),
                                   "outcomeOrStats": sum(row["grade"] == "UNRESOLVED" for row in rows)}}
        return {
            "league": "NBA", "season": self.season, "modelVersion": self.model_version,
            "predictions": rows, "metrics": metrics,
            "evaluationPolicy": {"predictionQualitySeparateFromBettingProfitability": True,
                                 "flatUnitWagers": True, "invalidOrPostTipoffMarketsExcluded": True},
        }
