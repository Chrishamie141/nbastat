"""Reproducible NBA game-winner diagnosis and research comparison.

This command is intentionally offline/research-only.  It reads a frozen
historical dataset, optionally retrieves the same bounded ESPN schedule window
used by the cloud ingestion pipeline, and writes analysis artifacts.  It does
not connect to the application database, persist predictions, promote a model,
or authorize wagering.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from backend.app.services.nba.game_model import (
    HOME_ADVANTAGE,
    INITIAL_RATING,
    K_FACTOR,
    _expected,
    update_ratings,
)
from backend.app.services.nba.schedule import fetch_schedule
from backend.app.services.nba.shadow import candidate_prediction, load_artifact
from backtesting.nba_model_quality import FEATURES, build_pregame_rows


CURRENT_ELO = "nba-game-elo-baseline-v1"
RESEARCH_LOGIT = "nba-game-logit-form-rest-research-v2"
ENHANCED_ELO = "nba-game-enhanced-elo-research-v1"
SCORE_FORM_GBT = "nba-game-score-form-gbt-research-v1"
HYBRID = "nba-game-elo-logit-hybrid-research-v1"


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _matrix(rows: list[dict]) -> np.ndarray:
    return np.asarray([[float(row[name]) for name in FEATURES] for row in rows], dtype=float)


def _labels(rows: list[dict]) -> np.ndarray:
    return np.asarray([int(row["home_win"]) for row in rows], dtype=int)


def _wilson(wins: int, total: int, z: float = 1.959963984540054) -> list[float] | None:
    if total <= 0:
        return None
    proportion = wins / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    radius = z * math.sqrt(
        proportion * (1 - proportion) / total + z * z / (4 * total * total)
    ) / denominator
    return [center - radius, center + radius]


def metrics(rows: list[dict], probability: np.ndarray) -> dict:
    y = _labels(rows)
    p = np.clip(np.asarray(probability, dtype=float), 1e-6, 1 - 1e-6)
    picks = p >= 0.5
    correct = picks == y
    home_mask = picks
    away_mask = ~picks
    buckets = []
    ece = 0.0
    for index in range(10):
        low, high = index / 10, (index + 1) / 10
        mask = (p >= low) & (p < high if index < 9 else p <= high)
        count = int(mask.sum())
        mean_probability = float(p[mask].mean()) if count else None
        observed = float(y[mask].mean()) if count else None
        if count:
            ece += count / len(y) * abs(float(mean_probability) - float(observed))
        buckets.append({
            "lower": low,
            "upper": high,
            "count": count,
            "averageHomeProbability": mean_probability,
            "actualHomeWinRate": observed,
        })
    wins = int(correct.sum())
    early = np.asarray([
        min(int(row.get("prior_home_games") or 0), int(row.get("prior_away_games") or 0)) < 10
        for row in rows
    ])
    return {
        "games": len(rows),
        "accuracy": float(correct.mean()),
        "accuracyWilson95": _wilson(wins, len(rows)),
        "brierScore": float(np.mean((p - y) ** 2)),
        "logLoss": float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))),
        "auc": float(roc_auc_score(y, p)),
        "expectedCalibrationError": float(ece),
        "homePickPct": float(home_mask.mean()),
        "homePickCount": int(home_mask.sum()),
        "awayPickCount": int(away_mask.sum()),
        "homePickAccuracy": float(correct[home_mask].mean()) if home_mask.any() else None,
        "awayPickAccuracy": float(correct[away_mask].mean()) if away_mask.any() else None,
        "earlySeasonGames": int(early.sum()),
        "earlySeasonAccuracy": float(correct[early].mean()) if early.any() else None,
        "laterSeasonGames": int((~early).sum()),
        "laterSeasonAccuracy": float(correct[~early].mean()) if (~early).any() else None,
        "calibrationBuckets": buckets,
    }


def _cluster_bootstrap(
    rows: list[dict], baseline: np.ndarray, candidate: np.ndarray, *, samples: int = 2000,
    seed: int = 20261029,
) -> dict:
    clusters: dict[tuple[int, int], list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        iso = _dt(row["kickoff_time"]).isocalendar()
        clusters[(iso.year, iso.week)].append(index)
    blocks = list(clusters.values())
    y = _labels(rows)
    rng = np.random.default_rng(seed)
    values = {"accuracy": [], "brierScore": [], "logLoss": []}
    for _ in range(samples):
        chosen = rng.integers(0, len(blocks), len(blocks))
        indices = np.asarray([i for block in chosen for i in blocks[block]], dtype=int)
        base = metrics([rows[i] for i in indices], baseline[indices])
        cand = metrics([rows[i] for i in indices], candidate[indices])
        values["accuracy"].append(cand["accuracy"] - base["accuracy"])
        values["brierScore"].append(base["brierScore"] - cand["brierScore"])
        values["logLoss"].append(base["logLoss"] - cand["logLoss"])
    return {
        "method": "paired calendar-week cluster bootstrap",
        "samples": samples,
        "clusters": len(blocks),
        "candidateImprovement": {
            name: {
                "estimate": float(np.mean(measurements)),
                "ci95": [
                    float(np.quantile(measurements, 0.025)),
                    float(np.quantile(measurements, 0.975)),
                ],
            }
            for name, measurements in values.items()
        },
    }


def _elo_probability(home: float, away: float, home_advantage: float) -> float:
    return 1.0 / (1.0 + 10.0 ** (-((home + home_advantage) - away) / 400.0))


def _enhanced_elo_predictions(games: list[dict], config: dict) -> dict[str, float]:
    ratings: dict[str, float] = {}
    predictions: dict[str, float] = {}
    current_season: str | None = None
    ordered = sorted(games, key=lambda row: (str(row["season"]), _dt(row["kickoff_time"]), str(row["game_id"])))
    for game in ordered:
        season = str(game["season"])
        if season != current_season:
            if current_season is not None:
                retention = float(config["carryoverRetention"])
                ratings = {
                    team: INITIAL_RATING + retention * (rating - INITIAL_RATING)
                    for team, rating in ratings.items()
                }
            current_season = season
        home, away = str(game["home_team"]), str(game["away_team"])
        home_rating = ratings.get(home, INITIAL_RATING)
        away_rating = ratings.get(away, INITIAL_RATING)
        probability = _elo_probability(home_rating, away_rating, float(config["homeAdvantage"]))
        predictions[str(game["game_id"])] = probability
        home_score, away_score = int(game["home_score"]), int(game["away_score"])
        actual = 1.0 if home_score > away_score else 0.0 if home_score < away_score else 0.5
        multiplier = 1.0
        if config["marginOfVictory"]:
            margin = abs(home_score - away_score)
            winner_difference = (
                (home_rating + float(config["homeAdvantage"])) - away_rating
                if home_score > away_score
                else away_rating - (home_rating + float(config["homeAdvantage"]))
            )
            multiplier = math.log(margin + 1.0) * 2.2 / (
                0.001 * abs(winner_difference) + 2.2
            )
        change = float(config["kFactor"]) * multiplier * (actual - probability)
        ratings[home] = home_rating + change
        ratings[away] = away_rating - change
    return predictions


def _tune_enhanced_elo(games: list[dict], validation_rows: list[dict]) -> tuple[dict, list[dict]]:
    trials = []
    for home_advantage in (35.0, 50.0, 65.0, 80.0):
        for k_factor in (10.0, 20.0, 30.0):
            for carryover in (0.0, 0.5, 0.75):
                for margin in (False, True):
                    config = {
                        "homeAdvantage": home_advantage,
                        "kFactor": k_factor,
                        "carryoverRetention": carryover,
                        "marginOfVictory": margin,
                    }
                    all_predictions = _enhanced_elo_predictions(games, config)
                    probability = np.asarray([
                        all_predictions[str(row["game_id"])] for row in validation_rows
                    ])
                    score = metrics(validation_rows, probability)
                    trials.append({"config": config, **{k: score[k] for k in (
                        "accuracy", "brierScore", "logLoss", "auc", "homePickPct"
                    )}})
    best = min(trials, key=lambda row: (
        row["brierScore"], row["logLoss"], -row["accuracy"],
        row["config"]["kFactor"], row["config"]["homeAdvantage"],
    ))
    return dict(best["config"]), trials


def _fit_logit(train: list[dict], c_value: float) -> Pipeline:
    model = Pipeline([
        ("scale", StandardScaler()),
        ("model", LogisticRegression(C=c_value, max_iter=2000, random_state=42)),
    ])
    model.fit(_matrix(train), _labels(train))
    return model


def _tune_logit(train: list[dict], validation: list[dict]) -> tuple[float, list[dict]]:
    trials = []
    for c_value in (0.01, 0.05, 0.1, 0.5, 1.0, 2.0, 10.0):
        probability = _fit_logit(train, c_value).predict_proba(_matrix(validation))[:, 1]
        score = metrics(validation, probability)
        trials.append({"C": c_value, **{k: score[k] for k in (
            "accuracy", "brierScore", "logLoss", "auc", "homePickPct"
        )}})
    selected = min(trials, key=lambda row: (row["brierScore"], row["logLoss"], row["C"]))
    return float(selected["C"]), trials


def _fit_gbt(train: list[dict], config: dict) -> GradientBoostingClassifier:
    model = GradientBoostingClassifier(random_state=42, **config)
    model.fit(_matrix(train), _labels(train))
    return model


def _tune_gbt(train: list[dict], validation: list[dict]) -> tuple[dict, list[dict]]:
    trials = []
    for depth in (1, 2, 3):
        for estimators in (50, 100):
            for rate in (0.03, 0.07):
                config = {
                    "max_depth": depth,
                    "n_estimators": estimators,
                    "learning_rate": rate,
                    "min_samples_leaf": 20,
                    "subsample": 0.8,
                }
                probability = _fit_gbt(train, config).predict_proba(_matrix(validation))[:, 1]
                score = metrics(validation, probability)
                trials.append({"config": config, **{k: score[k] for k in (
                    "accuracy", "brierScore", "logLoss", "auc", "homePickPct"
                )}})
    selected = min(trials, key=lambda row: (
        row["brierScore"], row["logLoss"], row["config"]["max_depth"],
        row["config"]["n_estimators"],
    ))
    return dict(selected["config"]), trials


def evaluate_history(dataset: dict) -> dict:
    games = list(dataset["games"])
    metadata = dict(dataset.get("metadata") or {})
    duplicate_ids = len(games) - len({str(game["game_id"]) for game in games})
    missing_scores = sum(
        game.get("home_score") is None or game.get("away_score") is None for game in games
    )
    rows = build_pregame_rows(games)
    seasons = ("2023-24", "2024-25", "2025-26")
    grouped = {season: [row for row in rows if row["season"] == season] for season in seasons}
    train, validation, test = grouped[seasons[0]], grouped[seasons[1]], grouped[seasons[2]]
    if any(not group for group in grouped.values()):
        raise ValueError("The frozen three-season split is incomplete")
    quality = {
        "declaredGames": metadata.get("gameCount"),
        "observedGames": len(games),
        "declaredHash": metadata.get("gamesSha256"),
        "observedHash": _canonical_hash(games),
        "datasetHashMatches": metadata.get("gamesSha256") == _canonical_hash(games),
        "duplicateGameIds": duplicate_ids,
        "missingScores": missing_scores,
        "ties": sum(int(game["home_score"]) == int(game["away_score"]) for game in games),
        "teams": len({str(game["home_team"]) for game in games} | {str(game["away_team"]) for game in games}),
        "seasonCounts": {season: len(grouped[season]) for season in seasons},
        "featureCutoffViolations": sum(
            bool(row.get("feature_cutoff")) and _dt(row["feature_cutoff"]) >= _dt(row["kickoff_time"])
            for row in rows
        ),
    }
    if duplicate_ids or missing_scores or not quality["datasetHashMatches"]:
        raise ValueError(f"Historical dataset failed integrity checks: {quality}")

    selected_c, logit_trials = _tune_logit(train, validation)
    validation_logit = _fit_logit(train, selected_c).predict_proba(_matrix(validation))[:, 1]
    logit_model = _fit_logit(train + validation, selected_c)
    test_logit = logit_model.predict_proba(_matrix(test))[:, 1]

    selected_gbt, gbt_trials = _tune_gbt(train, validation)
    validation_gbt = _fit_gbt(train, selected_gbt).predict_proba(_matrix(validation))[:, 1]
    test_gbt = _fit_gbt(train + validation, selected_gbt).predict_proba(_matrix(test))[:, 1]

    enhanced_config, enhanced_trials = _tune_enhanced_elo(games, validation)
    enhanced_all = _enhanced_elo_predictions(games, enhanced_config)
    validation_enhanced = np.asarray([
        enhanced_all[str(row["game_id"])] for row in validation
    ])
    test_enhanced = np.asarray([enhanced_all[str(row["game_id"])] for row in test])

    baseline_validation = np.asarray([row["baseline_home_probability"] for row in validation])
    baseline_test = np.asarray([row["baseline_home_probability"] for row in test])
    development_home_rate = float(_labels(train + validation).mean())
    always_home_test = np.repeat(development_home_rate, len(test))

    hybrid_trials = []
    for weight in np.linspace(0.0, 1.0, 21):
        probability = weight * validation_logit + (1.0 - weight) * validation_enhanced
        score = metrics(validation, probability)
        hybrid_trials.append({"logitWeight": float(weight), **{k: score[k] for k in (
            "accuracy", "brierScore", "logLoss", "auc", "homePickPct"
        )}})
    selected_weight = float(min(
        hybrid_trials, key=lambda row: (row["brierScore"], row["logLoss"], row["logitWeight"])
    )["logitWeight"])
    test_hybrid = selected_weight * test_logit + (1.0 - selected_weight) * test_enhanced

    probabilities = {
        "always_home": always_home_test,
        "current_elo": baseline_test,
        "corrected_elo": baseline_test.copy(),
        "enhanced_elo": test_enhanced,
        "team_statistics_gbt": test_gbt,
        "existing_research_candidate": test_logit,
        "hybrid_candidate": test_hybrid,
    }
    labels = {
        "always_home": "Always home (development home rate)",
        "current_elo": "Current Elo",
        "corrected_elo": "Verified/corrected Elo (no math change)",
        "enhanced_elo": "Enhanced Elo",
        "team_statistics_gbt": "Score/form gradient boost",
        "existing_research_candidate": "Existing form/rest logistic candidate",
        "hybrid_candidate": "Elo + logistic hybrid",
    }
    versions = {
        "always_home": "baseline-always-home",
        "current_elo": CURRENT_ELO,
        "corrected_elo": CURRENT_ELO,
        "enhanced_elo": ENHANCED_ELO,
        "team_statistics_gbt": SCORE_FORM_GBT,
        "existing_research_candidate": RESEARCH_LOGIT,
        "hybrid_candidate": HYBRID,
    }
    comparison = []
    for key, probability in probabilities.items():
        score = metrics(test, probability)
        comparison.append({
            "key": key,
            "model": labels[key],
            "modelVersion": versions[key],
            "evaluationPopulation": "2025-26 untouched test season",
            **{name: score[name] for name in (
                "games", "accuracy", "accuracyWilson95", "brierScore", "logLoss", "auc",
                "expectedCalibrationError", "homePickPct", "homePickCount", "awayPickCount",
                "homePickAccuracy", "awayPickAccuracy", "earlySeasonGames",
                "earlySeasonAccuracy", "laterSeasonGames", "laterSeasonAccuracy",
            )},
        })
    validation_metrics = {
        "current_elo": metrics(validation, baseline_validation),
        "enhanced_elo": metrics(validation, validation_enhanced),
        "team_statistics_gbt": metrics(validation, validation_gbt),
        "existing_research_candidate": metrics(validation, validation_logit),
    }
    uncertainty = {
        key: _cluster_bootstrap(test, baseline_test, probability)
        for key, probability in probabilities.items()
        if key not in {"current_elo", "corrected_elo"}
    }
    pairwise_uncertainty = {
        "hybridVsExistingResearchCandidate": _cluster_bootstrap(
            test, test_logit, test_hybrid
        ),
        "hybridVsEnhancedElo": _cluster_bootstrap(
            test, test_enhanced, test_hybrid
        ),
    }
    season_metrics = []
    seasonal_probabilities = {
        "2024-25": {
            "current_elo": baseline_validation,
            "enhanced_elo": validation_enhanced,
            "team_statistics_gbt": validation_gbt,
            "existing_research_candidate": validation_logit,
        },
        "2025-26": {
            "current_elo": baseline_test,
            "enhanced_elo": test_enhanced,
            "team_statistics_gbt": test_gbt,
            "existing_research_candidate": test_logit,
            "hybrid_candidate": test_hybrid,
        },
    }
    for season, model_probabilities in seasonal_probabilities.items():
        season_rows = validation if season == "2024-25" else test
        for key, probability in model_probabilities.items():
            score = metrics(season_rows, probability)
            season_metrics.append({
                "season": season,
                "role": "validation/model-selection" if season == "2024-25" else "untouched test",
                "model": key,
                **{name: score[name] for name in (
                    "games", "accuracy", "brierScore", "logLoss", "auc",
                    "expectedCalibrationError", "homePickPct",
                )},
            })
    calibration = {
        key: metrics(test, probability)["calibrationBuckets"]
        for key, probability in probabilities.items()
    }
    predictions = []
    for index, row in enumerate(test):
        predictions.append({
            "game_id": row["game_id"],
            "season": row["season"],
            "kickoff_time": row["kickoff_time"],
            "feature_cutoff": row["feature_cutoff"],
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "actual_home_win": int(row["home_win"]),
            **{f"{key}_home_probability": float(probability[index])
               for key, probability in probabilities.items()},
        })
    return {
        "policy": {
            "status": "RESEARCH_ONLY",
            "wageringAuthorized": False,
            "productionModelChanged": False,
            "trainSeason": seasons[0],
            "validationSeason": seasons[1],
            "testSeason": seasons[2],
            "heldOutTestUsedForSelection": False,
        },
        "dataQuality": quality,
        "comparison": comparison,
        "validationMetrics": validation_metrics,
        "seasonMetrics": season_metrics,
        "selectedConfigurations": {
            "existingResearchCandidate": {"C": selected_c},
            "enhancedElo": enhanced_config,
            "scoreFormGradientBoost": selected_gbt,
            "hybrid": {"logitWeight": selected_weight, "enhancedEloWeight": 1.0 - selected_weight},
        },
        "tuning": {
            "logit": logit_trials,
            "enhancedElo": enhanced_trials,
            "scoreFormGradientBoost": gbt_trials,
            "hybrid": hybrid_trials,
        },
        "uncertaintyVsCurrentElo": uncertainty,
        "pairwiseUncertainty": pairwise_uncertainty,
        "calibration": calibration,
        "predictions": predictions,
    }


def diagnose_current(
    *, season: str, start: date, end: date, generated_at: str, feature_cutoff: str,
) -> dict:
    rows = fetch_schedule(
        season=season, start=start, end=end, timeout=10, max_attempts=1,
    )
    regular = [row for row in rows if row.get("season_phase") == "regular_season"]
    finals = sorted(
        (row for row in regular if row.get("status") == "FINAL"),
        key=lambda row: (_dt(row["kickoff_time"]), str(row["game_id"])),
    )
    upcoming = sorted(
        (row for row in regular if row.get("status") == "SCHEDULED"
         and _dt(row["kickoff_time"]) > _dt(generated_at)),
        key=lambda row: (_dt(row["kickoff_time"]), str(row["game_id"])),
    )
    ratings: dict[str, float] = {}
    last_update: dict[str, dict] = {}
    histories: dict[str, list[dict]] = defaultdict(list)
    evidence: list[dict] = []
    for game in finals:
        evidence_game = {
            **game,
            "season_code": season,
            "retrieved_at": feature_cutoff,
            "source_hash": str(game["game_id"]),
        }
        update_ratings(ratings, evidence_game)
        home, away = str(game["home_team"]), str(game["away_team"])
        home_score, away_score = int(game["home_score"]), int(game["away_score"])
        histories[home].append({
            "kickoff_time": game["kickoff_time"],
            "margin": home_score - away_score,
            "won": float(home_score > away_score),
        })
        histories[away].append({
            "kickoff_time": game["kickoff_time"],
            "margin": away_score - home_score,
            "won": float(away_score > home_score),
        })
        evidence.append(evidence_game)
        for team in (str(game["home_team"]), str(game["away_team"])):
            last_update[team] = {
                "game_id": str(game["game_id"]),
                "kickoff_time": str(game["kickoff_time"]),
            }
    teams = sorted({
        str(row[side]) for row in regular for side in ("home_team", "away_team")
    })
    team_rows = [{
        "team": team,
        "elo_rating": round(ratings.get(team, INITIAL_RATING), 6),
        "last_completed_game_id": (last_update.get(team) or {}).get("game_id"),
        "last_completed_game_kickoff": (last_update.get(team) or {}).get("kickoff_time"),
        "eligible_final_games": len(finals),
    } for team in teams]
    forecast_rows = []
    candidate_rows = []
    artifact = load_artifact()
    for game in upcoming:
        home, away = str(game["home_team"]), str(game["away_team"])
        home_rating = ratings.get(home, INITIAL_RATING)
        away_rating = ratings.get(away, INITIAL_RATING)
        raw_difference = home_rating - away_rating
        adjusted_difference = raw_difference + HOME_ADVANTAGE
        home_probability = _expected(home_rating, away_rating)
        forecast_rows.append({
            "game_id": str(game["game_id"]),
            "kickoff_time": str(game["kickoff_time"]),
            "home_team": home,
            "away_team": away,
            "home_rating": round(home_rating, 6),
            "away_rating": round(away_rating, 6),
            "home_minus_away_rating": round(raw_difference, 6),
            "home_court_adjustment": HOME_ADVANTAGE,
            "adjusted_home_rating_difference": round(adjusted_difference, 6),
            "home_win_probability": round(home_probability, 6),
            "away_win_probability": round(1.0 - home_probability, 6),
            "predicted_winner": home if home_probability >= 0.5 else away,
            "selection_side": "HOME" if home_probability >= 0.5 else "AWAY",
            "feature_data_cutoff": feature_cutoff,
            "forecast_generated_at": generated_at,
            "model_version": CURRENT_ELO,
            "evidence_games": len(finals),
        })
        candidate = candidate_prediction(
            {**game, "season_code": season},
            ratings=ratings,
            histories=histories,
            evidence=evidence,
            generated_at=generated_at,
            artifact=artifact,
        )
        candidate_rows.append({
            "game_id": str(game["game_id"]),
            "kickoff_time": str(game["kickoff_time"]),
            "home_team": home,
            "away_team": away,
            "home_win_probability": candidate["home_probability"],
            "away_win_probability": candidate["away_probability"],
            "predicted_winner": candidate["selection"],
            "selection_side": "HOME" if candidate["selection"] == home else "AWAY",
            "feature_data_cutoff": candidate["feature_cutoff"],
            "forecast_generated_at": candidate["generated_at"],
            "model_version": candidate["model_version"],
            "evidence_games": candidate["evidence_games"],
            **candidate["features"],
        })
    return {
        "source": {
            "provider": "ESPN scoreboard adapter",
            "season": season,
            "windowStart": start.isoformat(),
            "windowEnd": end.isoformat(),
            "forecastGeneratedAt": generated_at,
            "productionFeatureCutoff": feature_cutoff,
        },
        "checks": {
            "recordsFetched": len(rows),
            "regularSeasonFinals": len(finals),
            "upcomingForecasts": len(forecast_rows),
            "teams": len(team_rows),
            "homeSelections": sum(row["selection_side"] == "HOME" for row in forecast_rows),
            "awaySelections": sum(row["selection_side"] == "AWAY" for row in forecast_rows),
            "researchCandidateHomeSelections": sum(
                row["selection_side"] == "HOME" for row in candidate_rows
            ),
            "researchCandidateAwaySelections": sum(
                row["selection_side"] == "AWAY" for row in candidate_rows
            ),
            "ratingRange": [
                min(row["elo_rating"] for row in team_rows),
                max(row["elo_rating"] for row in team_rows),
            ],
            "awaySelectionThreshold": "away Elo must exceed home Elo by more than 65 points",
            "equalRatingHomeProbability": _expected(INITIAL_RATING, INITIAL_RATING),
        },
        "teamRatings": team_rows,
        "forecasts": forecast_rows,
        "researchCandidateForecasts": candidate_rows,
        "scheduleRows": rows,
    }


def _write_csv(path: Path, rows: Iterable[dict]) -> None:
    rows = list(rows)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _flatten_comparison(rows: list[dict]) -> list[dict]:
    result = []
    for row in rows:
        flattened = dict(row)
        interval = flattened.pop("accuracyWilson95")
        flattened["accuracy_ci95_low"] = interval[0] if interval else None
        flattened["accuracy_ci95_high"] = interval[1] if interval else None
        result.append(flattened)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--current-season", default="2026-27")
    parser.add_argument("--current-start", type=date.fromisoformat, default=date(2026, 10, 1))
    parser.add_argument("--current-end", type=date.fromisoformat, default=date(2026, 11, 12))
    parser.add_argument("--generated-at", default="2026-10-29T20:53:34Z")
    parser.add_argument("--feature-cutoff", default="2026-10-29T10:29:40Z")
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    historical = evaluate_history(dataset)
    current = diagnose_current(
        season=args.current_season,
        start=args.current_start,
        end=args.current_end,
        generated_at=args.generated_at,
        feature_cutoff=args.feature_cutoff,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "historical-model-evaluation.json").write_text(
        json.dumps(historical, indent=2), encoding="utf-8"
    )
    (args.output_dir / "current-season-diagnosis.json").write_text(
        json.dumps(current, indent=2), encoding="utf-8"
    )
    (args.output_dir / "current-schedule-snapshot.json").write_text(
        json.dumps({"source": current["source"], "rows": current["scheduleRows"]}, indent=2),
        encoding="utf-8",
    )
    _write_csv(args.output_dir / "model-comparison.csv", _flatten_comparison(historical["comparison"]))
    _write_csv(args.output_dir / "held-out-predictions.csv", historical["predictions"])
    _write_csv(args.output_dir / "season-metrics.csv", historical["seasonMetrics"])
    _write_csv(args.output_dir / "current-team-ratings.csv", current["teamRatings"])
    _write_csv(args.output_dir / "upcoming-forecast-diagnostics.csv", current["forecasts"])
    _write_csv(
        args.output_dir / "upcoming-research-candidate-forecasts.csv",
        current["researchCandidateForecasts"],
    )
    calibration_rows = []
    for model, buckets in historical["calibration"].items():
        calibration_rows.extend({"model": model, **bucket} for bucket in buckets)
    _write_csv(args.output_dir / "calibration-buckets.csv", calibration_rows)
    summary = {
        "historicalDataQuality": historical["dataQuality"],
        "modelComparison": historical["comparison"],
        "selectedConfigurations": historical["selectedConfigurations"],
        "currentChecks": current["checks"],
        "policy": historical["policy"],
    }
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
