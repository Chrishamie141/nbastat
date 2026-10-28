"""Leakage-safe NBA game-winner model comparison.

The deployed Elo model is reproduced exactly.  The candidate is a small
regularized logistic model over pregame-only Elo, form, and rest features.  It
is research-only and this module has no path to production model selection.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from backend.app.services.nba.game_model import HOME_ADVANTAGE, INITIAL_RATING, K_FACTOR

BASELINE_VERSION = "nba-game-elo-baseline-v1"
CANDIDATE_VERSION = "nba-game-logit-form-rest-research-v2"
FEATURES = (
    "elo_difference", "recent_margin_difference", "season_margin_difference",
    "recent_win_rate_difference", "rest_advantage", "home_games", "away_games",
)


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _elo_probability(home: float, away: float) -> float:
    return 1.0 / (1.0 + 10.0 ** (-((home + HOME_ADVANTAGE) - away) / 400.0))


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def build_pregame_rows(games: list[dict], *, recent_games: int = 10) -> list[dict]:
    """Create features before applying each game's score to team state."""
    rows = []
    for season in sorted({str(game["season"]) for game in games}):
        ratings: dict[str, float] = {}
        histories: dict[str, list[dict[str, Any]]] = defaultdict(list)
        season_games = sorted((game for game in games if str(game["season"]) == season),
                              key=lambda row: (_dt(row["kickoff_time"]), str(row["game_id"])))
        for game in season_games:
            kickoff = _dt(game["kickoff_time"])
            home, away = str(game["home_team"]), str(game["away_team"])
            home_history, away_history = histories[home], histories[away]
            home_recent, away_recent = home_history[-recent_games:], away_history[-recent_games:]
            home_last = _dt(home_history[-1]["kickoff_time"]) if home_history else None
            away_last = _dt(away_history[-1]["kickoff_time"]) if away_history else None
            home_rest = min(7.0, (kickoff - home_last).total_seconds() / 86400.0) if home_last else 3.0
            away_rest = min(7.0, (kickoff - away_last).total_seconds() / 86400.0) if away_last else 3.0
            features = {
                "elo_difference": ratings.get(home, INITIAL_RATING) - ratings.get(away, INITIAL_RATING),
                "recent_margin_difference": _mean([r["margin"] for r in home_recent]) - _mean([r["margin"] for r in away_recent]),
                "season_margin_difference": _mean([r["margin"] for r in home_history]) - _mean([r["margin"] for r in away_history]),
                "recent_win_rate_difference": _mean([r["won"] for r in home_recent]) - _mean([r["won"] for r in away_recent]),
                "rest_advantage": max(-5.0, min(5.0, home_rest - away_rest)),
                "home_games": min(20, len(home_history)), "away_games": min(20, len(away_history)),
            }
            baseline_probability = _elo_probability(
                ratings.get(home, INITIAL_RATING), ratings.get(away, INITIAL_RATING)
            )
            prior_timestamps = [row["kickoff_time"] for row in home_history + away_history]
            home_score, away_score = int(game["home_score"]), int(game["away_score"])
            rows.append({
                "game_id": str(game["game_id"]), "season": season,
                "kickoff_time": game["kickoff_time"],
                "feature_cutoff": max(prior_timestamps, default=None),
                "home_team": home, "away_team": away, "home_win": int(home_score > away_score),
                "baseline_home_probability": baseline_probability, **features,
                "prior_home_games": len(home_history), "prior_away_games": len(away_history),
            })
            expected = baseline_probability
            actual = 1.0 if home_score > away_score else 0.0 if home_score < away_score else 0.5
            change = K_FACTOR * (actual - expected)
            ratings[home] = ratings.get(home, INITIAL_RATING) + change
            ratings[away] = ratings.get(away, INITIAL_RATING) - change
            histories[home].append({"kickoff_time": game["kickoff_time"],
                                    "margin": home_score - away_score, "won": float(home_score > away_score)})
            histories[away].append({"kickoff_time": game["kickoff_time"],
                                    "margin": away_score - home_score, "won": float(away_score > home_score)})
    return rows


def _matrix(rows: list[dict]) -> np.ndarray:
    return np.asarray([[float(row[name]) for name in FEATURES] for row in rows], dtype=float)


def _labels(rows: list[dict]) -> np.ndarray:
    return np.asarray([int(row["home_win"]) for row in rows], dtype=int)


def _metrics(y: np.ndarray, probability: np.ndarray) -> dict:
    p = np.clip(np.asarray(probability, dtype=float), 1e-6, 1 - 1e-6)
    y = np.asarray(y, dtype=int)
    bins = []
    ece = 0.0
    for low in np.arange(0.0, 1.0, 0.1):
        high = low + 0.1
        mask = (p >= low) & (p < high if high < 1 else p <= high)
        count = int(mask.sum())
        average = float(p[mask].mean()) if count else None
        actual = float(y[mask].mean()) if count else None
        if count:
            ece += count / len(y) * abs(average - actual)
        bins.append({"lower": round(float(low), 1), "upper": round(float(high), 1),
                     "count": count, "averageProbability": average, "actualHomeWinRate": actual})
    return {
        "games": int(len(y)), "accuracy": float(((p >= .5) == y).mean()),
        "brierScore": float(np.mean((p - y) ** 2)),
        "logLoss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
        "expectedCalibrationError": float(ece), "calibrationBuckets": bins,
    }


def _bootstrap_delta(y: np.ndarray, baseline: np.ndarray, candidate: np.ndarray,
                     *, samples: int = 2000, seed: int = 20261025) -> dict:
    rng = np.random.default_rng(seed)
    deltas = {"brierScore": [], "logLoss": [], "accuracy": []}
    for _ in range(samples):
        indices = rng.integers(0, len(y), len(y))
        base = _metrics(y[indices], baseline[indices])
        cand = _metrics(y[indices], candidate[indices])
        for metric in deltas:
            # Positive means candidate improvement for every reported delta.
            value = (base[metric] - cand[metric]) if metric != "accuracy" else (cand[metric] - base[metric])
            deltas[metric].append(value)
    return {metric: {
        "candidateImprovement": float(np.mean(values)),
        "ci95": [float(np.quantile(values, .025)), float(np.quantile(values, .975))],
    } for metric, values in deltas.items()}


def evaluate(games: list[dict], *, train_season: str, validation_season: str,
             test_season: str) -> dict:
    rows = build_pregame_rows(games)
    groups = {season: [row for row in rows if row["season"] == season]
              for season in (train_season, validation_season, test_season)}
    if any(not group for group in groups.values()):
        raise ValueError("train, validation, and test seasons must all contain final games")
    candidate_cs = (0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 10.0)
    tuning = []
    for c_value in candidate_cs:
        model = Pipeline([("scale", StandardScaler()),
                          ("model", LogisticRegression(C=c_value, max_iter=2000, random_state=42))])
        model.fit(_matrix(groups[train_season]), _labels(groups[train_season]))
        probability = model.predict_proba(_matrix(groups[validation_season]))[:, 1]
        tuning.append({"C": c_value, **_metrics(_labels(groups[validation_season]), probability)})
    selected = min(tuning, key=lambda row: (row["brierScore"], row["logLoss"], row["C"]))
    development = groups[train_season] + groups[validation_season]
    model = Pipeline([("scale", StandardScaler()),
                      ("model", LogisticRegression(C=selected["C"], max_iter=2000, random_state=42))])
    model.fit(_matrix(development), _labels(development))
    test = groups[test_season]
    y = _labels(test)
    baseline = np.asarray([row["baseline_home_probability"] for row in test])
    candidate = model.predict_proba(_matrix(test))[:, 1]
    coefficients = dict(zip(FEATURES, model.named_steps["model"].coef_[0].tolist()))
    scaler = model.named_steps["scale"]
    classifier = model.named_steps["model"]
    model_artifact = {
        "modelVersion": CANDIDATE_VERSION,
        "status": "RESEARCH_ONLY",
        "wageringAuthorized": False,
        "features": list(FEATURES),
        "selectedC": float(selected["C"]),
        "scalerMean": scaler.mean_.tolist(),
        "scalerScale": scaler.scale_.tolist(),
        "intercept": float(classifier.intercept_[0]),
        "coefficients": classifier.coef_[0].tolist(),
        "trainingSeasons": [train_season, validation_season],
        "heldOutSeason": test_season,
        "datasetGamesSha256": hashlib.sha256(json.dumps(
            sorted(str(game["game_id"]) for game in games),
            separators=(",", ":"),
        ).encode("utf-8")).hexdigest(),
    }
    return {
        "policy": {
            "status": "RESEARCH_ONLY", "wageringAuthorized": False,
            "productionModelChanged": False, "trainSeason": train_season,
            "validationSeason": validation_season, "testSeason": test_season,
            "selectionMetric": "validation Brier score", "heldOutTestUsedForSelection": False,
        },
        "sample": {season: len(group) for season, group in groups.items()},
        "baseline": {"modelVersion": BASELINE_VERSION, **_metrics(y, baseline)},
        "candidate": {"modelVersion": CANDIDATE_VERSION, "selectedC": selected["C"],
                      "coefficients": coefficients, **_metrics(y, candidate)},
        "modelArtifact": model_artifact,
        "bootstrapDeltas": _bootstrap_delta(y, baseline, candidate),
        "validationTuning": tuning,
        "predictions": [{
            "gameId": row["game_id"], "season": row["season"], "kickoffTime": row["kickoff_time"],
            "actualHomeWin": int(actual), "baselineHomeProbability": float(base),
            "candidateHomeProbability": float(cand),
        } for row, actual, base, cand in zip(test, y, baseline, candidate)],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--train-season", required=True)
    parser.add_argument("--validation-season", required=True)
    parser.add_argument("--test-season", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    report = evaluate(dataset["games"], train_season=args.train_season,
                      validation_season=args.validation_season, test_season=args.test_season)
    report["dataset"] = dataset["metadata"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"baseline": report["baseline"], "candidate": report["candidate"],
                      "bootstrapDeltas": report["bootstrapDeltas"]}, indent=2))


if __name__ == "__main__":
    main()
