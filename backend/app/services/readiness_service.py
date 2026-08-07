from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.app.database import database_path, get_db_connection, table_exists, using_postgres

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parents[3]
REQUIRED_TABLES = ("users", "predictions", "graded_bets", "parlay_history")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def database_health() -> dict[str, Any]:
    started = time.perf_counter()
    result: dict[str, Any] = {
        "status": "unhealthy",
        "engine": "postgres" if using_postgres() else "sqlite",
        "connectionOpened": False,
        "readSucceeded": False,
        "requiredTables": list(REQUIRED_TABLES),
        "missingTables": [],
    }
    try:
        with get_db_connection() as connection:
            result["connectionOpened"] = True
            connection.execute("SELECT 1").fetchone()
            result["readSucceeded"] = True
            result["missingTables"] = [table for table in REQUIRED_TABLES if not table_exists(connection, table)]
        result["status"] = "healthy" if not result["missingTables"] else "degraded"
    except Exception as exc:
        logger.exception("database_health_failed")
        result["errorCode"] = "DATABASE_UNAVAILABLE"
        result["error"] = "Database connection or read check failed."
        result["errorType"] = type(exc).__name__
    result["latencyMs"] = round((time.perf_counter() - started) * 1000, 1)
    if not using_postgres():
        result["storeExists"] = database_path().exists()
    return result


def prediction_store_health() -> dict[str, Any]:
    root = BASE_DIR / "backtesting" / "data" / "snapshots" / "nfl"
    artifacts = sorted(root.glob("*/week_*/player_prop_predictions.json"))
    readable = 0
    latest = artifacts[-1] if artifacts else None
    # Read the newest artifact fully. Counting all files is cheap, but parsing
    # every multi-megabyte weekly artifact made a health request unbounded.
    for path in ([latest] if latest else []):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                readable += 1
        except (OSError, json.JSONDecodeError):
            continue
    return {
        "status": "healthy" if readable else "degraded",
        "readableArtifacts": readable,
        "checkedArtifacts": 1 if latest else 0,
        "artifactCount": len(artifacts),
        "latestArtifact": str(latest.relative_to(BASE_DIR)) if latest else None,
    }


def readiness_report() -> dict[str, Any]:
    database = database_health()
    predictions = prediction_store_health()
    required_ok = database["status"] != "unhealthy"
    status = "healthy" if required_ok and predictions["status"] == "healthy" else "degraded" if required_ok else "unhealthy"
    snapshots = sorted((BASE_DIR / "backtesting" / "data" / "snapshots" / "nfl").glob("*/week_*/team_stats.json"))
    seasons = sorted({int(path.parents[1].name) for path in snapshots if path.parents[1].name.isdigit()})
    return {
        "status": status,
        "generatedAt": _utc_now(),
        "version": "2.0.0",
        "database": database,
        "predictionStore": predictions,
        "historicalContextStore": {"status": "healthy" if snapshots else "degraded", "seasons": seasons, "artifactCount": len(snapshots)},
        "gameStatusSource": {"status": "configured", "provider": "ESPN free scoreboard/summary", "liveProbePerformed": False},
        "dependencyPolicy": {"database": "REQUIRED", "predictionStore": "DEGRADED_ALLOWED", "historicalContextStore": "DEGRADED_ALLOWED", "weather": "OPTIONAL"},
    }


def startup_self_check() -> dict[str, Any]:
    report = readiness_report()
    logger.info("startup_readiness %s", json.dumps(report, sort_keys=True))
    return report
