"""Prospective, research-only NBA shadow predictions.

The shadow candidate is deliberately separate from the live Elo read model.
It writes immutable snapshots for the same games only when explicitly enabled,
and it cannot authorize wagers or become a champion through this module.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

from backend.app.database import get_db_connection
from .game_model import INITIAL_RATING, K_FACTOR, update_ratings
from .lifecycle import freeze_prediction, initialize
from .registry import NBA_GAME_RESEARCH_CANDIDATE_VERSION
from .season import normalize_nba_season


ARTIFACT_PATH = Path(__file__).with_name("research_artifacts") / (
    NBA_GAME_RESEARCH_CANDIDATE_VERSION + ".json"
)
FEATURE_NAMES = (
    "elo_difference",
    "recent_margin_difference",
    "season_margin_difference",
    "recent_win_rate_difference",
    "rest_advantage",
    "home_games",
    "away_games",
)


def _dt(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def shadow_enabled() -> bool:
    return str(os.getenv("NBA_SHADOW_ENABLED") or "").strip().lower() in {
        "1", "true", "yes", "on",
    }


@lru_cache(maxsize=1)
def load_artifact() -> dict:
    artifact = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    if artifact.get("modelVersion") != NBA_GAME_RESEARCH_CANDIDATE_VERSION:
        raise RuntimeError("NBA shadow artifact model version is invalid")
    if artifact.get("status") != "RESEARCH_ONLY" or artifact.get("wageringAuthorized") is not False:
        raise RuntimeError("NBA shadow artifact violates research-only governance")
    if tuple(artifact.get("features") or ()) != FEATURE_NAMES:
        raise RuntimeError("NBA shadow artifact feature contract is invalid")
    supplied_hash = str(artifact.get("artifactSha256") or "")
    hash_body = {key: value for key, value in artifact.items() if key != "artifactSha256"}
    calculated = hashlib.sha256(_canonical(hash_body).encode("utf-8")).hexdigest()
    if not supplied_hash or supplied_hash != calculated:
        raise RuntimeError("NBA shadow artifact integrity check failed")
    return artifact


def _features(*, game: dict, ratings: dict[str, float], histories: dict[str, list[dict]]) -> dict:
    kickoff = _dt(game["kickoff_time"])
    home, away = str(game["home_team"]), str(game["away_team"])
    home_history, away_history = histories[home], histories[away]
    home_recent, away_recent = home_history[-10:], away_history[-10:]
    home_rest = min(
        7.0, (kickoff - _dt(home_history[-1]["kickoff_time"])).total_seconds() / 86400.0
    ) if home_history else 3.0
    away_rest = min(
        7.0, (kickoff - _dt(away_history[-1]["kickoff_time"])).total_seconds() / 86400.0
    ) if away_history else 3.0
    return {
        "elo_difference": ratings.get(home, INITIAL_RATING) - ratings.get(away, INITIAL_RATING),
        "recent_margin_difference": _mean([row["margin"] for row in home_recent])
        - _mean([row["margin"] for row in away_recent]),
        "season_margin_difference": _mean([row["margin"] for row in home_history])
        - _mean([row["margin"] for row in away_history]),
        "recent_win_rate_difference": _mean([row["won"] for row in home_recent])
        - _mean([row["won"] for row in away_recent]),
        "rest_advantage": max(-5.0, min(5.0, home_rest - away_rest)),
        "home_games": min(20, len(home_history)),
        "away_games": min(20, len(away_history)),
    }


def _probability(features: dict, artifact: dict) -> float:
    standardized = [
        (float(features[name]) - float(mean)) / float(scale)
        for name, mean, scale in zip(
            FEATURE_NAMES, artifact["scalerMean"], artifact["scalerScale"]
        )
    ]
    logit = float(artifact["intercept"]) + sum(
        coefficient * value
        for coefficient, value in zip(artifact["coefficients"], standardized)
    )
    if logit >= 0:
        return 1.0 / (1.0 + math.exp(-logit))
    exp_value = math.exp(logit)
    return exp_value / (1.0 + exp_value)


def candidate_prediction(
    game: dict, *, ratings: dict[str, float], histories: dict[str, list[dict]],
    evidence: list[dict], generated_at: str, artifact: dict | None = None,
) -> dict:
    artifact = artifact or load_artifact()
    if _dt(generated_at) >= _dt(game["kickoff_time"]):
        raise ValueError("NBA shadow predictions must be generated before tipoff")
    features = _features(game=game, ratings=ratings, histories=histories)
    home_probability = _probability(features, artifact)
    home, away = str(game["home_team"]), str(game["away_team"])
    selection = home if home_probability >= 0.5 else away
    evidence_hashes = [str(row.get("source_hash") or "") for row in evidence]
    data_version = hashlib.sha256(_canonical({
        "artifactSha256": artifact["artifactSha256"],
        "evidence": evidence_hashes,
    }).encode("utf-8")).hexdigest()
    evidence_through = max(
        (str(row["kickoff_time"]) for row in evidence), default=None,
    )
    return {
        "game_id": str(game["game_id"]),
        "season": str(game["season_code"]),
        "kickoff_time": str(game["kickoff_time"]),
        "market": "moneyline",
        "selection": selection,
        "line": None,
        "model_probability": round(
            home_probability if selection == home else 1.0 - home_probability, 6
        ),
        "projection": None,
        "edge": None,
        "market_odds": None,
        "model_version": NBA_GAME_RESEARCH_CANDIDATE_VERSION,
        "data_version": data_version,
        "generated_at": generated_at,
        "feature_cutoff": generated_at,
        "source_provider": "espn_scoreboard+frozen_research_artifact",
        "model_status": "RESEARCH_ONLY",
        "wager_authorized": False,
        "shadow_only": True,
        "home_probability": round(home_probability, 6),
        "away_probability": round(1.0 - home_probability, 6),
        "features": features,
        "artifact_sha256": artifact["artifactSha256"],
        "evidence_games": len(evidence),
        "evidence_through": evidence_through,
        "methodology": "prospective research shadow; no injury feature; not production-approved",
    }


def generate_shadow_predictions(*, season: str | int, at: datetime | None = None) -> dict:
    """Freeze candidate snapshots without changing the live Elo prediction path."""
    if not shadow_enabled():
        return {
            "enabled": False,
            "modelVersion": NBA_GAME_RESEARCH_CANDIDATE_VERSION,
            "modelStatus": "RESEARCH_ONLY",
            "productionReady": False,
            "created": 0,
            "alreadyPresent": 0,
            "startedGamesSkipped": 0,
        }
    initialize()
    artifact = load_artifact()
    season_code = normalize_nba_season(season).code
    clock = (at or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    generated_at = clock.isoformat().replace("+00:00", "Z")
    with get_db_connection() as connection:
        games = [dict(row) for row in connection.execute(
            "SELECT * FROM nba_games WHERE season_code=? ORDER BY kickoff_time,game_id",
            (season_code,),
        ).fetchall()]
        existing = {str(row["game_id"]) for row in connection.execute(
            """SELECT game_id FROM nba_prediction_snapshots
               WHERE season_code=? AND user_id=0 AND market='moneyline' AND model_version=?""",
            (season_code, NBA_GAME_RESEARCH_CANDIDATE_VERSION),
        ).fetchall()}
    ratings: dict[str, float] = {}
    histories: dict[str, list[dict]] = defaultdict(list)
    evidence: list[dict] = []
    created: list[str] = []
    started = already = 0
    for game in games:
        kickoff = _dt(game["kickoff_time"])
        status = str(game.get("status") or "").upper()
        if status == "FINAL" and kickoff < clock:
            if str(game.get("season_phase") or "unknown") == "regular_season":
                update_ratings(ratings, game)
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
                evidence.append(game)
            continue
        if status != "SCHEDULED":
            continue
        if kickoff <= clock:
            started += 1
            continue
        if str(game["game_id"]) in existing:
            already += 1
            continue
        prediction = candidate_prediction(
            game, ratings=ratings, histories=histories, evidence=evidence,
            generated_at=generated_at, artifact=artifact,
        )
        created.append(freeze_prediction(prediction, user_id=0))
        existing.add(str(game["game_id"]))
    return {
        "enabled": True,
        "modelVersion": NBA_GAME_RESEARCH_CANDIDATE_VERSION,
        "modelStatus": "RESEARCH_ONLY",
        "productionReady": False,
        "artifactSha256": artifact["artifactSha256"],
        "created": len(created),
        "snapshotIds": created,
        "alreadyPresent": already,
        "startedGamesSkipped": started,
    }


def _model_metrics(rows: list[dict]) -> dict:
    settled = [row for row in rows if row.get("home_score") is not None and row.get("away_score") is not None]
    if not settled:
        return {
            "predictions": len(rows), "settled": 0, "accuracy": None,
            "brierScore": None, "logLoss": None, "expectedCalibrationError": None,
        }
    outcomes = [int(int(row["home_score"]) > int(row["away_score"])) for row in settled]
    probabilities = [min(1 - 1e-6, max(1e-6, float(row["home_probability"]))) for row in settled]
    accuracy = sum((probability >= 0.5) == bool(actual) for probability, actual in zip(probabilities, outcomes)) / len(settled)
    brier = sum((probability - actual) ** 2 for probability, actual in zip(probabilities, outcomes)) / len(settled)
    log_loss = -sum(
        actual * math.log(probability) + (1 - actual) * math.log(1 - probability)
        for probability, actual in zip(probabilities, outcomes)
    ) / len(settled)
    ece = 0.0
    for index in range(10):
        low, high = index / 10, (index + 1) / 10
        bucket = [
            (probability, actual) for probability, actual in zip(probabilities, outcomes)
            if probability >= low and (probability < high if index < 9 else probability <= high)
        ]
        if bucket:
            average_probability = sum(row[0] for row in bucket) / len(bucket)
            observed = sum(row[1] for row in bucket) / len(bucket)
            ece += len(bucket) / len(settled) * abs(average_probability - observed)
    return {
        "predictions": len(rows), "settled": len(settled),
        "accuracy": round(accuracy, 6), "brierScore": round(brier, 6),
        "logLoss": round(log_loss, 6), "expectedCalibrationError": round(ece, 6),
    }


def shadow_comparison(*, season: str | int) -> dict:
    """Return like-for-like prospective metrics and candidate/baseline disagreements."""
    initialize()
    season_code = normalize_nba_season(season).code
    with get_db_connection() as connection:
        raw_rows = connection.execute(
            """SELECT p.game_id,p.model_version,p.generated_at,p.selection,p.payload_json,
                      g.kickoff_time,g.status,g.home_team,g.away_team,g.home_score,g.away_score
               FROM nba_prediction_snapshots p JOIN nba_games g ON g.game_id=p.game_id
               WHERE p.season_code=? AND p.user_id=0 AND p.market='moneyline'
                 AND p.model_version IN (?,?)
               ORDER BY g.kickoff_time,p.model_version""",
            (season_code, "nba-game-elo-baseline-v1", NBA_GAME_RESEARCH_CANDIDATE_VERSION),
        ).fetchall()
    by_game: dict[str, dict[str, dict]] = defaultdict(dict)
    by_model: dict[str, list[dict]] = defaultdict(list)
    for raw in raw_rows:
        row = dict(raw)
        payload = json.loads(row.pop("payload_json") or "{}")
        row["home_probability"] = payload.get("home_probability")
        by_game[str(row["game_id"])][str(row["model_version"])] = row
        by_model[str(row["model_version"])].append(row)
    paired = [models for models in by_game.values() if len(models) == 2]
    disagreements = []
    probability_gaps = []
    for models in paired:
        baseline = models["nba-game-elo-baseline-v1"]
        candidate = models[NBA_GAME_RESEARCH_CANDIDATE_VERSION]
        probability_gaps.append(abs(float(baseline["home_probability"]) - float(candidate["home_probability"])))
        if baseline["selection"] != candidate["selection"]:
            disagreements.append({
                "gameId": baseline["game_id"], "kickoffTime": baseline["kickoff_time"],
                "matchup": f"{baseline['away_team']} at {baseline['home_team']}",
                "baselineSelection": baseline["selection"],
                "candidateSelection": candidate["selection"],
                "baselineGeneratedAt": baseline["generated_at"],
                "candidateGeneratedAt": candidate["generated_at"],
                "baselineHomeProbability": baseline["home_probability"],
                "candidateHomeProbability": candidate["home_probability"],
            })
    return {
        "enabled": shadow_enabled(), "status": "RESEARCH_ONLY", "productionReady": False,
        "baselineModel": "nba-game-elo-baseline-v1",
        "candidateModel": NBA_GAME_RESEARCH_CANDIDATE_VERSION,
        "pairedGames": len(paired), "disagreements": len(disagreements),
        "disagreementRate": round(len(disagreements) / len(paired), 6) if paired else None,
        "meanAbsoluteHomeProbabilityGap": round(sum(probability_gaps) / len(probability_gaps), 6) if probability_gaps else None,
        "metricsByModel": {model: _model_metrics(rows) for model, rows in by_model.items()},
        "recentDisagreements": disagreements[:25],
    }
