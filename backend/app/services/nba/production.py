"""NBA research prediction generation and read models."""

from __future__ import annotations

from datetime import datetime, timezone
import json

from backend.app.database import get_db_connection
from .game_model import NBA_GAME_MODEL_VERSION, update_ratings, winner_prediction
from .lifecycle import freeze_prediction, initialize
from .season import normalize_nba_season
from .shadow import shadow_comparison


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def generate_winner_predictions(*, season: str | int, at: datetime | None = None) -> dict:
    """Freeze one system-owned research prediction for each future game."""
    initialize()
    season_code = normalize_nba_season(season).code
    clock = (at or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    generated_at = clock.isoformat().replace("+00:00", "Z")
    with get_db_connection() as connection:
        games = [dict(row) for row in connection.execute(
            "SELECT * FROM nba_games WHERE season_code=? ORDER BY kickoff_time,game_id",
            (season_code,),
        ).fetchall()]
        existing = {str(row["game_id"]): str(row["snapshot_id"]) for row in connection.execute(
            """SELECT snapshot_id,game_id FROM nba_prediction_snapshots
               WHERE season_code=? AND user_id=0 AND market='moneyline' AND model_version=?""",
            (season_code, NBA_GAME_MODEL_VERSION),
        ).fetchall()}
    ratings: dict[str, float] = {}
    evidence: list[dict] = []
    created: list[str] = []
    skipped_started = skipped_existing = 0
    for game in games:
        kickoff = _dt(game["kickoff_time"])
        if str(game.get("status") or "").upper() == "FINAL" and kickoff < clock:
            if str(game.get("season_phase") or "unknown") == "regular_season":
                update_ratings(ratings, game)
                evidence.append(game)
            continue
        if str(game.get("status") or "").upper() != "SCHEDULED":
            continue
        if kickoff <= clock:
            skipped_started += 1
            continue
        if game["game_id"] in existing:
            skipped_existing += 1
            continue
        prediction = winner_prediction(game, ratings=ratings, evidence=evidence, generated_at=generated_at)
        created.append(freeze_prediction(prediction, user_id=0))
    return {
        "modelVersion": NBA_GAME_MODEL_VERSION, "modelStatus": "RESEARCH_ONLY",
        "productionReady": False, "created": len(created), "snapshotIds": created,
        "alreadyPresent": skipped_existing, "startedGamesSkipped": skipped_started,
    }

def prediction_history(*, season: str | int | None = None, user_id: int | None = None,
                       game_id: str | None = None,
                       include_shadow: bool = False,
                       limit: int = 500) -> list[dict]:
    """Return immutable NBA lifecycle rows.

    Customer-facing callers receive only the established Elo baseline. The
    research shadow is available solely to explicit internal comparison paths.
    """
    initialize()
    where, values = [], []
    if season is not None:
        where.append("p.season_code=?")
        values.append(normalize_nba_season(season).code)
    if user_id is not None:
        where.append("p.user_id IN (0,?)")
        values.append(int(user_id))
    if game_id is not None:
        where.append("p.game_id=?")
        values.append(str(game_id))
    if not include_shadow:
        where.append("p.model_version=?")
        values.append(NBA_GAME_MODEL_VERSION)
    clause = " WHERE " + " AND ".join(where) if where else ""
    values.append(int(limit))
    with get_db_connection() as connection:
        rows = connection.execute(
            """SELECT p.*,g.home_team,g.away_team,g.status AS game_status,g.home_score,g.away_score,
                      g.retrieved_at AS data_retrieved_at,s.status AS settlement_status,
                      s.actual_value,s.reason,s.settled_at
               FROM nba_prediction_snapshots p
               LEFT JOIN nba_games g ON g.game_id=p.game_id
               LEFT JOIN nba_settlements s ON s.prediction_snapshot_id=p.snapshot_id"""
            + clause + " ORDER BY p.kickoff_time DESC,p.generated_at DESC LIMIT ?",
            tuple(values),
        ).fetchall()
    result = []
    for raw in rows:
        row, payload = dict(raw), json.loads(raw["payload_json"] or "{}")
        status = row.get("settlement_status") or "UNRESOLVED"
        result.append({
            "id": row["snapshot_id"], "league": "NBA", "sport": "NBA",
            "gameId": row["game_id"], "season": row["season_code"],
            "kickoffTime": row["kickoff_time"], "homeTeam": row.get("home_team"),
            "awayTeam": row.get("away_team"), "market": row["market"],
            "predictedOutcome": row["selection"], "predictedWinner": row["selection"],
            "estimatedProbability": row["model_probability"], "modelProbability": row["model_probability"],
            "modelName": row["model_version"], "modelVersion": row["model_version"],
            "modelStatus": payload.get("model_status", "RESEARCH_ONLY"),
            "createdAt": row["generated_at"], "dataTimestamp": row["feature_cutoff"],
            "actualOutcome": row.get("actual_value"), "settlementStatus": status,
            "evaluationMetadata": {"reason": row.get("reason"), "settledAt": row.get("settled_at")},
            "homeScore": row.get("home_score"), "awayScore": row.get("away_score"),
            "gameStatus": row.get("game_status"), "dataRetrievedAt": row.get("data_retrieved_at"),
            "wagerAuthorized": bool(payload.get("wager_authorized", False)),
            "homeWinProbability": payload.get("home_probability"),
            "awayWinProbability": payload.get("away_probability"),
        })
    return result


def _performance_for_rows(rows: list[dict]) -> dict:
    settled = [row for row in rows if row["settlementStatus"] in {"WIN", "LOSS", "PUSH"}]
    decisions = [row for row in settled if row["settlementStatus"] in {"WIN", "LOSS"}]
    wins = sum(row["settlementStatus"] == "WIN" for row in decisions)
    return {
        "predictions": len(rows), "settled": len(settled), "unresolved": sum(
            row["settlementStatus"] == "UNRESOLVED" for row in rows
        ), "invalidExcluded": sum(row["settlementStatus"] == "VOID" for row in rows),
        "wins": wins, "losses": len(decisions) - wins,
        "pushes": sum(row["settlementStatus"] == "PUSH" for row in settled),
        "accuracy": round(wins / len(decisions), 4) if decisions else None,
    }


def performance(*, season: str | int | None = None) -> dict:
    rows = prediction_history(
        season=season, user_id=None, include_shadow=True, limit=10000,
    )
    by_model: dict[str, list[dict]] = {}
    for row in rows:
        by_model.setdefault(str(row["modelVersion"]), []).append(row)
    # Preserve the existing live read model: top-level NBA performance remains
    # the Elo baseline even when an explicitly enabled research shadow exists.
    baseline = _performance_for_rows(by_model.get(NBA_GAME_MODEL_VERSION, []))
    comparison = shadow_comparison(season=season) if season is not None else None
    return {
        **baseline, "modelVersion": NBA_GAME_MODEL_VERSION,
        "modelStatus": "RESEARCH_ONLY", "productionReady": False,
        "byModel": {model: _performance_for_rows(model_rows)
                    for model, model_rows in by_model.items()},
        "shadowComparison": comparison,
    }


def games_board(*, season: str | int, limit: int = 100) -> dict:
    season_code = normalize_nba_season(season).code
    rows = prediction_history(season=season_code, user_id=0, limit=max(limit * 2, 100))
    prediction_by_game = {}
    shadows_by_game: dict[str, list[dict]] = {}
    for row in rows:
        if row["modelVersion"] == NBA_GAME_MODEL_VERSION:
            prediction_by_game.setdefault(row["gameId"], row)
        else:
            shadows_by_game.setdefault(row["gameId"], []).append(row)
    with get_db_connection() as connection:
        games = [dict(row) for row in connection.execute(
            "SELECT * FROM nba_games WHERE season_code=? ORDER BY kickoff_time DESC LIMIT ?",
            (season_code, int(limit)),
        ).fetchall()]
    items = []
    for game in games:
        prediction = prediction_by_game.get(game["game_id"])
        items.append({
            "gameId": game["game_id"], "league": "NBA", "season": season_code,
            "seasonPhase": game.get("season_phase"), "kickoffTime": game["kickoff_time"],
            "homeTeam": game["home_team"], "awayTeam": game["away_team"],
            "status": game["status"], "homeScore": game.get("home_score"),
            "awayScore": game.get("away_score"), "dataTimestamp": game["retrieved_at"],
            "prediction": prediction,
            "shadowPredictions": shadows_by_game.get(game["game_id"], []),
        })
    return {"league": "NBA", "season": season_code, "items": items,
            "modelStatus": "RESEARCH_ONLY", "productionReady": False}


def game_detail(*, season: str | int, game_id: str) -> dict | None:
    """Read one synchronized game without truncating the season first."""
    initialize()
    season_code = normalize_nba_season(season).code
    with get_db_connection() as connection:
        raw = connection.execute(
            "SELECT * FROM nba_games WHERE season_code=? AND game_id=?",
            (season_code, str(game_id)),
        ).fetchone()
    if raw is None:
        return None
    game = dict(raw)
    predictions = prediction_history(season=season_code, user_id=0, game_id=game_id, limit=10)
    baseline = next((row for row in predictions if row["modelVersion"] == NBA_GAME_MODEL_VERSION), None)
    shadows = [row for row in predictions if row["modelVersion"] != NBA_GAME_MODEL_VERSION]
    return {
        "gameId": game["game_id"], "league": "NBA", "season": season_code,
        "seasonPhase": game.get("season_phase"), "kickoffTime": game["kickoff_time"],
        "homeTeam": game["home_team"], "awayTeam": game["away_team"],
        "status": game["status"], "homeScore": game.get("home_score"),
        "awayScore": game.get("away_score"), "dataTimestamp": game["retrieved_at"],
        "prediction": baseline, "shadowPredictions": shadows,
        "productionReady": False, "modelStatus": "RESEARCH_ONLY",
    }

