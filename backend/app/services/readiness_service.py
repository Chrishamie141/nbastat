from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.app.database import database_path, get_db_connection, table_exists, using_postgres
from database_safety import isolated_tests

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parents[3]
POSTGRES_CORE_TABLES = ("users", "parlay_history")
POSTGRES_NFL_TABLES = (
    "nfl_automation_events", "nfl_automation_state", "nfl_game_predictions",
    "nfl_game_results", "nfl_game_schedule_state", "nfl_prediction_history",
)
POSTGRES_NBA_TABLES = (
    "nba_automation_runs", "nba_games", "nba_model_registry",
    "nba_operational_settings", "nba_prediction_snapshots", "nba_settlements",
)
SQLITE_APPLICATION_TABLES = ("users", "parlay_history")
LEGACY_SQLITE_TABLES = ("predictions", "bet_recommendations", "graded_bets", "parlay_history")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _store_status(required: tuple[str, ...], missing: list[str], *, engine: str,
                  purpose: str, required_for_runtime: bool = True) -> dict[str, Any]:
    return {
        "status": "healthy" if not missing else "degraded",
        "engine": engine,
        "purpose": purpose,
        "requiredForRuntime": required_for_runtime,
        "requiredTables": list(required),
        "missingTables": missing,
    }


def legacy_sqlite_health() -> dict[str, Any]:
    """Inspect the checked-in legacy store without creating or changing it."""
    started = time.perf_counter()
    path = (Path(os.environ["SMARTBETS_TEST_SQLITE_PATH"]) if isolated_tests()
            and os.getenv("SMARTBETS_TEST_SQLITE_PATH") else BASE_DIR / "predictions.db")
    result: dict[str, Any] = {
        "status": "not_present",
        "engine": "sqlite",
        "purpose": "legacy player predictions and graded bets",
        "requiredForRuntime": False,
        "storeExists": path.exists(),
        "requiredTables": list(LEGACY_SQLITE_TABLES),
        "missingTables": list(LEGACY_SQLITE_TABLES),
        "readSucceeded": False,
    }
    if not path.exists():
        result["latencyMs"] = round((time.perf_counter() - started) * 1000, 1)
        return result
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2)
        try:
            connection.execute("SELECT 1").fetchone()
            names = {
                str(row[0]) for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        finally:
            connection.close()
        result["readSucceeded"] = True
        result["missingTables"] = [table for table in LEGACY_SQLITE_TABLES if table not in names]
        result["status"] = "healthy" if not result["missingTables"] else "degraded"
    except (OSError, sqlite3.Error) as exc:
        result["status"] = "unhealthy"
        result["errorCode"] = "LEGACY_SQLITE_UNREADABLE"
        result["errorType"] = type(exc).__name__
    result["latencyMs"] = round((time.perf_counter() - started) * 1000, 1)
    return result


def database_health() -> dict[str, Any]:
    started = time.perf_counter()
    postgres = using_postgres()
    groups = {
        "corePostgres": POSTGRES_CORE_TABLES,
        "nflPostgres": POSTGRES_NFL_TABLES,
        "nbaPostgres": POSTGRES_NBA_TABLES,
    } if postgres else {"applicationSqlite": SQLITE_APPLICATION_TABLES}
    required_tables = tuple(table for tables in groups.values() for table in tables)
    result: dict[str, Any] = {
        "status": "unhealthy",
        "engine": "postgres" if postgres else "sqlite",
        "connectionOpened": False,
        "readSucceeded": False,
        "requiredTables": list(required_tables),
        "missingTables": [],
        "stores": {},
    }
    try:
        with get_db_connection() as connection:
            result["connectionOpened"] = True
            connection.execute("SELECT 1").fetchone()
            result["readSucceeded"] = True
            for name, tables in groups.items():
                missing = [table for table in tables if not table_exists(connection, table)]
                purpose = {
                    "corePostgres": "authentication and account-owned application data",
                    "nflPostgres": "NFL V2 schedule, predictions, results, and automation",
                    "nbaPostgres": "NBA research predictions, settlement, and automation",
                    "applicationSqlite": "local application and legacy prediction data",
                }[name]
                result["stores"][name] = _store_status(
                    tables, missing, engine="postgres" if postgres else "sqlite", purpose=purpose
                )
                result["missingTables"].extend(missing)
        result["status"] = "healthy" if not result["missingTables"] else "degraded"
    except Exception as exc:
        logger.exception("database_health_failed")
        result["errorCode"] = "DATABASE_UNAVAILABLE"
        result["error"] = "Database connection or read check failed."
        result["errorType"] = type(exc).__name__
    result["latencyMs"] = round((time.perf_counter() - started) * 1000, 1)
    if not postgres:
        result["storeExists"] = database_path().exists()
    result["stores"]["legacySqlite"] = legacy_sqlite_health()
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
    if not readable:
        runtime_path = BASE_DIR / "data" / "nfl_recent_player_stats.json"
        try:
            payload = json.loads(runtime_path.read_text(encoding="utf-8"))
            if isinstance(payload.get("players"), dict) and payload["players"]:
                readable = 1
                latest = runtime_path
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
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
    context_count = len(snapshots)
    if not snapshots:
        try:
            runtime_rows = json.loads((BASE_DIR / "data" / "nfl_team_context_history.json").read_text(encoding="utf-8"))
            seasons = sorted({int(row["season"]) for row in runtime_rows})
            context_count = len(runtime_rows)
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            pass
    return {
        "status": status,
        "generatedAt": _utc_now(),
        "version": "2.0.0",
        "database": database,
        "predictionStore": predictions,
        "historicalContextStore": {"status": "healthy" if context_count else "degraded", "seasons": seasons, "artifactCount": context_count},
        "gameStatusSource": {"status": "configured", "provider": "ESPN free scoreboard/summary", "liveProbePerformed": False},
        "dependencyPolicy": {"database": "REQUIRED", "predictionStore": "DEGRADED_ALLOWED", "historicalContextStore": "DEGRADED_ALLOWED", "weather": "OPTIONAL"},
    }


def startup_self_check() -> dict[str, Any]:
    report = readiness_report()
    logger.info("startup_readiness %s", json.dumps(report, sort_keys=True))
    return report
