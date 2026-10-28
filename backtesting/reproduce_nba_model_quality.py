"""Independent, chronology-audited reproduction of the NBA research candidate.

This module intentionally does not import ``backtesting.nba_model_quality``.
It reconstructs the state machine, split, fit, metrics, and uncertainty from
the versioned raw game snapshot so agreement is not a comparison of one
function with itself.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


BASELINE_VERSION = "nba-game-elo-baseline-v1"
CANDIDATE_VERSION = "nba-game-logit-form-rest-research-v2"
FEATURE_NAMES = (
    "elo_difference",
    "recent_margin_difference",
    "season_margin_difference",
    "recent_win_rate_difference",
    "rest_advantage",
    "home_games",
    "away_games",
)
INITIAL_RATING = 1500.0
HOME_ADVANTAGE = 65.0
K_FACTOR = 20.0


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def average(values: list[float]) -> float:
    return float(sum(values) / len(values)) if values else 0.0


def elo_home_probability(home_rating: float, away_rating: float) -> float:
    exponent = -((home_rating + HOME_ADVANTAGE) - away_rating) / 400.0
    return 1.0 / (1.0 + math.pow(10.0, exponent))


def reconstruct_rows(games: list[dict], recent_window: int = 10) -> list[dict]:
    """Rebuild every feature from state available strictly before tipoff."""
    output: list[dict] = []
    for season in sorted({str(game["season"]) for game in games}):
        ratings: dict[str, float] = {}
        history: dict[str, list[dict]] = defaultdict(list)
        season_games = sorted(
            (game for game in games if str(game["season"]) == season),
            key=lambda game: (parse_time(game["kickoff_time"]), str(game["game_id"])),
        )
        for game in season_games:
            tip = parse_time(game["kickoff_time"])
            home = str(game["home_team"])
            away = str(game["away_team"])
            home_prior = history[home]
            away_prior = history[away]
            home_recent = home_prior[-recent_window:]
            away_recent = away_prior[-recent_window:]
            home_rest = min(
                7.0,
                (tip - parse_time(home_prior[-1]["kickoff_time"])).total_seconds() / 86400.0,
            ) if home_prior else 3.0
            away_rest = min(
                7.0,
                (tip - parse_time(away_prior[-1]["kickoff_time"])).total_seconds() / 86400.0,
            ) if away_prior else 3.0
            feature_cutoff = max(
                [row["kickoff_time"] for row in home_prior + away_prior],
                default=None,
            )
            baseline = elo_home_probability(
                ratings.get(home, INITIAL_RATING), ratings.get(away, INITIAL_RATING)
            )
            row = {
                "game_id": str(game["game_id"]),
                "season": season,
                "kickoff_time": str(game["kickoff_time"]),
                "feature_cutoff": feature_cutoff,
                "home_team": home,
                "away_team": away,
                "home_win": int(int(game["home_score"]) > int(game["away_score"])),
                "baseline_home_probability": baseline,
                "elo_difference": ratings.get(home, INITIAL_RATING) - ratings.get(away, INITIAL_RATING),
                "recent_margin_difference": average([r["margin"] for r in home_recent])
                - average([r["margin"] for r in away_recent]),
                "season_margin_difference": average([r["margin"] for r in home_prior])
                - average([r["margin"] for r in away_prior]),
                "recent_win_rate_difference": average([r["win"] for r in home_recent])
                - average([r["win"] for r in away_recent]),
                "rest_advantage": max(-5.0, min(5.0, home_rest - away_rest)),
                "home_games": min(20, len(home_prior)),
                "away_games": min(20, len(away_prior)),
            }
            output.append(row)

            home_score = int(game["home_score"])
            away_score = int(game["away_score"])
            actual = 1.0 if home_score > away_score else 0.0 if home_score < away_score else 0.5
            change = K_FACTOR * (actual - baseline)
            ratings[home] = ratings.get(home, INITIAL_RATING) + change
            ratings[away] = ratings.get(away, INITIAL_RATING) - change
            home_record = {
                "kickoff_time": str(game["kickoff_time"]),
                "margin": home_score - away_score,
                "win": float(home_score > away_score),
            }
            away_record = {
                "kickoff_time": str(game["kickoff_time"]),
                "margin": away_score - home_score,
                "win": float(away_score > home_score),
            }
            history[home].append(home_record)
            history[away].append(away_record)
    return output


def matrix(rows: list[dict]) -> np.ndarray:
    return np.asarray([[float(row[name]) for name in FEATURE_NAMES] for row in rows], dtype=float)


def labels(rows: list[dict]) -> np.ndarray:
    return np.asarray([int(row["home_win"]) for row in rows], dtype=int)


def metrics(y_true: np.ndarray, probability: np.ndarray) -> dict:
    y = np.asarray(y_true, dtype=int)
    p = np.clip(np.asarray(probability, dtype=float), 1e-6, 1.0 - 1e-6)
    accuracy = float(np.mean((p >= 0.5) == y))
    brier = float(np.mean(np.square(p - y)))
    log_loss = float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
    calibration = []
    ece = 0.0
    for index in range(10):
        low, high = index / 10, (index + 1) / 10
        selected = (p >= low) & ((p < high) if index < 9 else (p <= high))
        count = int(selected.sum())
        mean_probability = float(p[selected].mean()) if count else None
        observed = float(y[selected].mean()) if count else None
        if count:
            ece += count / len(y) * abs(mean_probability - observed)
        calibration.append({
            "lower": low,
            "upper": high,
            "count": count,
            "averageProbability": mean_probability,
            "actualHomeWinRate": observed,
        })
    return {
        "games": int(len(y)),
        "accuracy": accuracy,
        "brierScore": brier,
        "logLoss": log_loss,
        "expectedCalibrationError": float(ece),
        "calibrationBuckets": calibration,
    }


def wilson_interval(wins: int, total: int, z: float = 1.959963984540054) -> list[float]:
    proportion = wins / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    radius = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total)) / denominator
    return [float(center - radius), float(center + radius)]


def clustered_uncertainty(
    rows: list[dict], y: np.ndarray, baseline: np.ndarray, candidate: np.ndarray,
    samples: int = 4000, seed: int = 20261025,
) -> dict:
    """Paired calendar-week cluster bootstrap to retain short-range dependence."""
    clusters: dict[tuple[int, int], list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        iso = parse_time(row["kickoff_time"]).isocalendar()
        clusters[(iso.year, iso.week)].append(index)
    blocks = list(clusters.values())
    rng = np.random.default_rng(seed)
    deltas = {"accuracy": [], "brierScore": [], "logLoss": []}
    model_values = {
        "baseline": {"accuracy": [], "brierScore": [], "logLoss": []},
        "candidate": {"accuracy": [], "brierScore": [], "logLoss": []},
    }
    for _ in range(samples):
        selected_blocks = rng.integers(0, len(blocks), len(blocks))
        indices = np.asarray([idx for block in selected_blocks for idx in blocks[block]], dtype=int)
        base_metrics = metrics(y[indices], baseline[indices])
        candidate_metrics = metrics(y[indices], candidate[indices])
        for metric in deltas:
            deltas[metric].append(
                candidate_metrics[metric] - base_metrics[metric]
                if metric == "accuracy"
                else base_metrics[metric] - candidate_metrics[metric]
            )
            model_values["baseline"][metric].append(base_metrics[metric])
            model_values["candidate"][metric].append(candidate_metrics[metric])
    return {
        "method": "paired calendar-week cluster bootstrap",
        "samples": samples,
        "clusters": len(blocks),
        "candidateImprovement": {
            metric: {
                "estimate": float(np.mean(values)),
                "ci95": [float(np.quantile(values, 0.025)), float(np.quantile(values, 0.975))],
            }
            for metric, values in deltas.items()
        },
        "modelIntervals": {
            model: {
                metric: [float(np.quantile(values, 0.025)), float(np.quantile(values, 0.975))]
                for metric, values in model_metrics.items()
            }
            for model, model_metrics in model_values.items()
        },
    }


def canonical_games_hash(games: list[dict]) -> str:
    body = json.dumps(games, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def reproduce(dataset: dict, reference: dict | None = None) -> tuple[dict, dict]:
    games = list(dataset["games"])
    metadata = dict(dataset["metadata"])
    checks = {
        "declaredGameCountMatches": int(metadata.get("gameCount", -1)) == len(games),
        "datasetHashMatches": metadata.get("gamesSha256") == canonical_games_hash(games),
        "duplicateGameIds": len(games) - len({str(game["game_id"]) for game in games}),
        "missingScores": sum(game.get("home_score") is None or game.get("away_score") is None for game in games),
        "ties": sum(int(game["home_score"]) == int(game["away_score"]) for game in games),
    }
    rows = reconstruct_rows(games)
    seasons = ("2023-24", "2024-25", "2025-26")
    groups = {season: [row for row in rows if row["season"] == season] for season in seasons}
    if any(not group for group in groups.values()):
        raise ValueError("independent reproduction requires all three frozen seasons")
    leakage_rows = [
        row["game_id"] for row in rows
        if row["feature_cutoff"] and parse_time(row["feature_cutoff"]) >= parse_time(row["kickoff_time"])
    ]
    ranges = {
        season: {
            "firstTip": min(row["kickoff_time"] for row in group),
            "lastTip": max(row["kickoff_time"] for row in group),
            "games": len(group),
        }
        for season, group in groups.items()
    }
    checks.update({
        "featureCutoffViolations": len(leakage_rows),
        "strictSeasonOrdering": parse_time(ranges[seasons[0]]["lastTip"])
        < parse_time(ranges[seasons[1]]["firstTip"])
        < parse_time(ranges[seasons[1]]["lastTip"])
        < parse_time(ranges[seasons[2]]["firstTip"]),
        "splitGameOverlap": len(
            set(row["game_id"] for row in groups[seasons[0]])
            & set(row["game_id"] for row in groups[seasons[1]] + groups[seasons[2]])
        ) + len(
            set(row["game_id"] for row in groups[seasons[1]])
            & set(row["game_id"] for row in groups[seasons[2]])
        ),
    })
    if not checks["declaredGameCountMatches"] or not checks["datasetHashMatches"]:
        raise ValueError("dataset receipt does not match its declared immutable content")
    if checks["duplicateGameIds"] or checks["missingScores"] or checks["ties"]:
        raise ValueError("dataset integrity check failed")
    if checks["featureCutoffViolations"] or not checks["strictSeasonOrdering"] or checks["splitGameOverlap"]:
        raise ValueError("chronology or split-integrity check failed")

    train, validation, test = groups[seasons[0]], groups[seasons[1]], groups[seasons[2]]
    tuning = []
    for c_value in (0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 10.0):
        pipeline = Pipeline([
            ("scale", StandardScaler()),
            ("model", LogisticRegression(C=c_value, max_iter=2000, random_state=42)),
        ])
        pipeline.fit(matrix(train), labels(train))
        validation_probability = pipeline.predict_proba(matrix(validation))[:, 1]
        tuning.append({"C": c_value, **metrics(labels(validation), validation_probability)})
    selected = min(tuning, key=lambda item: (item["brierScore"], item["logLoss"], item["C"]))
    development = train + validation
    fitted = Pipeline([
        ("scale", StandardScaler()),
        ("model", LogisticRegression(C=selected["C"], max_iter=2000, random_state=42)),
    ])
    fitted.fit(matrix(development), labels(development))
    y = labels(test)
    baseline_probability = np.asarray([row["baseline_home_probability"] for row in test], dtype=float)
    candidate_probability = fitted.predict_proba(matrix(test))[:, 1]
    baseline_metrics = metrics(y, baseline_probability)
    candidate_metrics = metrics(y, candidate_probability)
    baseline_metrics["accuracyWilson95"] = wilson_interval(
        int(np.sum((baseline_probability >= 0.5) == y)), len(y)
    )
    candidate_metrics["accuracyWilson95"] = wilson_interval(
        int(np.sum((candidate_probability >= 0.5) == y)), len(y)
    )

    reference_check = None
    if reference:
        reference_predictions = {str(row["gameId"]): row for row in reference.get("predictions", [])}
        maximum_baseline_delta = max(
            abs(float(reference_predictions[row["game_id"]]["baselineHomeProbability"]) - float(base))
            for row, base in zip(test, baseline_probability)
        )
        maximum_candidate_delta = max(
            abs(float(reference_predictions[row["game_id"]]["candidateHomeProbability"]) - float(candidate))
            for row, candidate in zip(test, candidate_probability)
        )
        reference_check = {
            "gameIdsMatch": set(reference_predictions) == {row["game_id"] for row in test},
            "maximumBaselineProbabilityDelta": maximum_baseline_delta,
            "maximumCandidateProbabilityDelta": maximum_candidate_delta,
            "selectedCMatches": float(reference["candidate"]["selectedC"]) == float(selected["C"]),
        }

    scaler = fitted.named_steps["scale"]
    model = fitted.named_steps["model"]
    artifact = {
        "modelVersion": CANDIDATE_VERSION,
        "status": "RESEARCH_ONLY",
        "wageringAuthorized": False,
        "features": list(FEATURE_NAMES),
        "selectedC": float(selected["C"]),
        "scalerMean": scaler.mean_.tolist(),
        "scalerScale": scaler.scale_.tolist(),
        "intercept": float(model.intercept_[0]),
        "coefficients": model.coef_[0].tolist(),
        "trainingSeasons": [seasons[0], seasons[1]],
        "heldOutSeason": seasons[2],
        "sourceGamesSha256": metadata["gamesSha256"],
        "featurePolicy": "strictly pregame team state; current score applied only after feature emission",
    }
    artifact["artifactSha256"] = hashlib.sha256(
        json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    report = {
        "policy": {
            "status": "RESEARCH_ONLY",
            "wageringAuthorized": False,
            "productionModelChanged": False,
            "trainSeason": seasons[0],
            "validationSeason": seasons[1],
            "testSeason": seasons[2],
            "heldOutTestUsedForSelection": False,
            "selectionMetric": "validation Brier score",
        },
        "dataset": metadata,
        "checks": checks,
        "seasonRanges": ranges,
        "tuning": tuning,
        "selectedC": float(selected["C"]),
        "baseline": {"modelVersion": BASELINE_VERSION, **baseline_metrics},
        "candidate": {"modelVersion": CANDIDATE_VERSION, **candidate_metrics},
        "uncertainty": clustered_uncertainty(test, y, baseline_probability, candidate_probability),
        "referenceAgreement": reference_check,
        "predictions": [
            {
                "gameId": row["game_id"],
                "kickoffTime": row["kickoff_time"],
                "actualHomeWin": int(actual),
                "baselineHomeProbability": float(base),
                "candidateHomeProbability": float(candidate),
            }
            for row, actual, base, candidate in zip(test, y, baseline_probability, candidate_probability)
        ],
    }
    return report, artifact


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    reference = json.loads(args.reference.read_text(encoding="utf-8")) if args.reference else None
    report, artifact = reproduce(dataset, reference)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.artifact.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    args.artifact.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(json.dumps({
        "checks": report["checks"],
        "referenceAgreement": report["referenceAgreement"],
        "baseline": report["baseline"],
        "candidate": report["candidate"],
        "uncertainty": report["uncertainty"]["candidateImprovement"],
        "artifactSha256": artifact["artifactSha256"],
    }, indent=2))


if __name__ == "__main__":
    main()
