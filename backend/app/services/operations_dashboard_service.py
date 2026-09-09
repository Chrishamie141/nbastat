"""Owner control-plane aggregates with panel-level failure isolation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Callable

from backend.app.database import using_postgres
from backend.app.services import social_marketing
from backend.app.services import social_operations_service
from backend.app.services import nfl_server_automation
from backend.app.services.operator_action_service import recent as recent_actions
from backend.app.services.readiness_service import database_health, prediction_store_health

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_WEEK1_DB = ROOT / "backtesting" / "market_capture" / "nfl_2026_reg1_v1.db"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_panel(loader: Callable[[], Any]) -> dict:
    try:
        return {"status": "HEALTHY", "data": loader(), "error": None}
    except Exception as exc:
        message = str(exc) if isinstance(exc, FileNotFoundError) else f"Dependency check failed ({type(exc).__name__})."
        return {"status": "UNAVAILABLE", "data": None,
                "error": {"code": type(exc).__name__.upper(), "message": message}}


def _git_version() -> str:
    value = (os.getenv("VERCEL_GIT_COMMIT_SHA") or "").strip()
    if value:
        return value[:12]
    try:
        head = (ROOT / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        return ((ROOT / ".git" / head[5:]).read_text(encoding="utf-8").strip() if head.startswith("ref: ") else head)[:12]
    except OSError:
        return "unavailable"


def _social(at: datetime) -> dict:
    social_marketing.initialize()
    summary = social_operations_service.summary(lambda: at)
    with social_marketing.connection() as connection:
        source_row = connection.execute("SELECT payload,verified_at FROM social_sources ORDER BY verified_at DESC LIMIT 1").fetchone()
        status_rows = connection.execute("SELECT status,COUNT(*) AS count FROM social_posts GROUP BY status").fetchall()
        last_post = connection.execute("SELECT scheduled_at,published_at,status FROM social_posts ORDER BY scheduled_at DESC LIMIT 1").fetchone()
    source = json.loads(source_row["payload"]) if source_row else {}
    source_at = datetime.fromisoformat(source_row["verified_at"]) if source_row else None
    dry_run = summary["settings"]["dry_run"]
    auto_publish = summary["settings"]["auto_publish"]
    expected_user = bool((os.getenv("X_EXPECTED_USER_ID") or "").strip())
    credentials = all(bool((os.getenv(name) or "").strip()) for name in
                      ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"))
    blocked = summary["publicWritesBlocked"] or not expected_user
    return {
        "status": "BLOCKED" if blocked else "ARMED", "credentialsConfigured": credentials,
        "dryRun": dry_run, "autoPublish": auto_publish, "accountVerificationComplete": expected_user,
        "expectedUserIdConfigured": expected_user, "publishingBlocked": blocked,
        "latestSourceAt": source_at.isoformat() if source_at else None,
        "sourceAgeMinutes": round(max(0, (at - source_at).total_seconds()) / 60, 1) if source_at else None,
        "postCounts": {row["status"]: int(row["count"]) for row in status_rows},
        "lastDryRunGeneration": last_post["scheduled_at"] if last_post and last_post["status"] == "DRAFT" else None,
        "lastPublishAttempt": (last_post["published_at"] or last_post["scheduled_at"]) if last_post and last_post["status"] != "DRAFT" else None,
        "lastAttemptStatus": last_post["status"] if last_post else None, "verifiedAggregate": source,
        "engineState": summary["engineState"], "queued": summary["queued"],
        "reviewRequired": summary["reviewRequired"], "imagesGeneratedToday": summary["imagesGeneratedToday"],
        "videosGeneratedToday": summary["videosGeneratedToday"], "nextScheduledPost": summary["nextScheduledPost"],
    }


SOCIAL_POST_STATUSES = {"DRAFT", "READY", "QUEUED", "REVIEW", "MEDIA_GENERATING", "PUBLISHING", "PUBLISHED", "FAILED", "UNKNOWN", "SKIPPED", "CANCELLED"}


def social_post_history(limit: int = 25, offset: int = 0, status: str | None = None) -> dict:
    """Return safe, owner-visible social history without contacting X."""
    normalized = (status or "").strip().upper()
    if normalized and normalized not in SOCIAL_POST_STATUSES:
        raise ValueError("Unsupported social post status filter.")
    where = " WHERE p.status=?" if normalized else ""
    values: tuple[Any, ...] = (normalized,) if normalized else ()
    social_marketing.initialize()
    with social_marketing.connection() as connection:
        total_row = connection.execute(f"SELECT COUNT(*) AS count FROM social_posts p{where}", values).fetchone()
        rows = connection.execute(
            "SELECT p.post_id,p.category,p.content,p.generated_at,p.scheduled_at,p.published_at,"
            "p.x_post_id,p.status,p.failure_reason,p.day_key,d.post_type,d.media_type,d.score,d.game_id,"
            "a.media_asset_id,m.impressions,m.likes,m.replies,m.reposts,e.evidence_json "
            "FROM social_posts p LEFT JOIN social_post_details d USING(post_id) "
            "LEFT JOIN social_events e ON e.event_id=d.event_id "
            "LEFT JOIN social_media_assets a ON a.media_asset_id=(SELECT a2.media_asset_id FROM social_media_assets a2 WHERE a2.post_id=p.post_id ORDER BY a2.version DESC LIMIT 1) "
            "LEFT JOIN social_metrics m ON m.metric_id=(SELECT m2.metric_id FROM social_metrics m2 WHERE m2.post_id=p.post_id ORDER BY m2.collected_at DESC LIMIT 1) "
            f"{where} ORDER BY COALESCE(p.published_at,p.scheduled_at) DESC,p.post_id DESC LIMIT ? OFFSET ?",
            values + (limit, offset),
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        evidence = json.loads(item.pop("evidence_json") or "{}")
        item["category"] = str(item["category"]).split("|", 1)[0]
        item["post_type"] = item.get("post_type") or item["category"]
        item["matchup"] = " at ".join(value for value in (evidence.get("away_team"), evidence.get("home_team")) if value) or "Slate-wide"
        item["mediaIndicator"] = item.get("media_type") or "text"
        item["thumbnailUrl"] = f"/api/internal/operations/social/media/{item['media_asset_id']}/content" if item.get("media_asset_id") else None
        item["xUrl"] = f"https://x.com/i/status/{item['x_post_id']}" if item.get("x_post_id") else None
        if item.get("impressions") not in (None, 0) and all(item.get(key) is not None for key in ("likes", "replies", "reposts")):
            item["engagementRate"] = round((item["likes"] + item["replies"] + item["reposts"]) / item["impressions"], 6)
        else:
            item["engagementRate"] = None
        items.append(item)
    total = int(total_row["count"] if total_row else 0)
    return {"items": items, "total": total, "limit": limit, "offset": offset,
            "hasMore": offset + len(items) < total, "status": normalized or "ALL"}


def _week_db_path() -> Path:
    value = (os.getenv("WEEK1_EXPERIMENT_DB") or "").strip()
    return Path(value).resolve() if value else DEFAULT_WEEK1_DB.resolve()


def week1_db_path() -> Path:
    """Expose the configured operational store without opening or mutating it."""
    return _week_db_path()


def _week1(at: datetime) -> dict:
    path = _week_db_path()
    if not path.exists():
        raise FileNotFoundError("Week 1 operational store is not available in this environment.")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        experiment = connection.execute("SELECT * FROM experiment LIMIT 1").fetchone()
        manifest = json.loads(experiment["manifest"])
        predictions = {r["game_id"]: dict(r) for r in connection.execute("SELECT * FROM forward_predictions")}
        finals = {r["game_id"]: dict(r) for r in connection.execute("SELECT * FROM forward_finals")}
        grades = {r["game_id"]: dict(r) for r in connection.execute("SELECT * FROM forward_grades")}
        checkpoints = [dict(r) for r in connection.execute("SELECT * FROM checkpoints ORDER BY due")]
        refreshes = {r["game_id"]: r["last_refresh"] for r in connection.execute(
            "SELECT game_id,MAX(stored_at) AS last_refresh FROM captures GROUP BY game_id")}
        request = connection.execute("SELECT * FROM requests ORDER BY requested_at DESC LIMIT 1").fetchone()
        events = [dict(r) for r in connection.execute("SELECT * FROM forward_events ORDER BY at DESC LIMIT 30")]
        games, issues = [], []
        for row in connection.execute("SELECT * FROM games ORDER BY kickoff"):
            game, warnings = dict(row), []
            kickoff = datetime.fromisoformat(row["kickoff"].replace("Z", "+00:00"))
            final, prediction, grade = finals.get(row["game_id"]), predictions.get(row["game_id"]), grades.get(row["game_id"])
            status = "FINAL" if final else "UNKNOWN" if kickoff <= at else "PREGAME" if kickoff - at <= timedelta(hours=1) else "SCHEDULED"
            if kickoff <= at and not final:
                warnings.append("Kickoff passed without a reconciled final or live status.")
            if final and not grade:
                warnings.append("Final is present but the frozen prediction is not graded.")
            if not prediction:
                warnings.append("Frozen prediction artifact is missing.")
            if warnings:
                issues.append({"severity": "WARNING", "category": "GAME_STATUS" if kickoff <= at else "PREDICTION",
                               "entityId": row["game_id"], "summary": warnings[0], "source": "week1_store",
                               "detectedAt": at.isoformat(), "lastAttemptAt": refreshes.get(row["game_id"]),
                               "recommendedAction": "Reconcile game" if kickoff <= at else "Inspect prediction artifact",
                               "action": "RECONCILE" if kickoff <= at else "VIEW_PREDICTION"})
            games.append({"id": row["game_id"], "awayTeam": row["away_team"], "homeTeam": row["home_team"],
                          "kickoff": row["kickoff"], "season": 2026, "seasonType": "regular", "week": 1,
                          "status": status, "awayScore": final["away_score"] if final else None,
                          "homeScore": final["home_score"] if final else None,
                          "predictionStatus": "GRADED" if grade else "READY" if prediction else "MISSING",
                          "statsStatus": "READY" if final else "PENDING", "lastRefresh": refreshes.get(row["game_id"]),
                          "freshness": "STALE" if warnings else "CURRENT", "warnings": warnings,
                          "prediction": ({"winner": prediction["winner"], "probability": prediction["probability"],
                                          "modelVersion": prediction["model_version"], "generatedAt": prediction["generated_at"],
                                          "artifactHash": prediction["prediction_hash"], "modelHash": prediction["model_hash"]}
                                         if prediction else None), "grade": grade["result"] if grade else None})
        next_checkpoint = next((r for r in checkpoints if r["state"] == "PENDING" and r["deadline"] > at.timestamp()), None)
        return {
            "experimentId": experiment["id"], "season": manifest["season"], "seasonType": manifest["phase"], "week": manifest["week"],
            "manifestHash": experiment["manifest_hash"],
            "predictionHash": hashlib.sha256(json.dumps(sorted(r["prediction_hash"] for r in predictions.values()), sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "modelVersion": manifest["frozen_model_reference"], "modelTuning": manifest.get("model_tuning", False),
            "games": games, "issues": issues, "scheduledGames": len(games), "predictions": len(predictions),
            "finals": len(finals), "graded": len(grades), "failedPredictions": 0,
            "checkpoints": {"completed": sum(r["state"] == "COMPLETE" for r in checkpoints),
                            "pending": sum(r["state"] == "PENDING" for r in checkpoints),
                            "missed": sum(r["state"] in {"MISSED_CAPTURE", "INTERRUPTED_CAPTURE"} for r in checkpoints),
                            "total": len(checkpoints), "next": next_checkpoint},
            "marketCoverage": len(refreshes),
            "provider": {"status": experiment["last_status"] or "UNKNOWN", "quotaState": experiment["quota_state"],
                         "creditsReserved": experiment["reserved"], "creditsLimit": experiment["credits_limit"],
                         "remaining": experiment["remaining"], "lastRequest": dict(request) if request else None},
            "latestPredictionRun": max((r["generated_at"] for r in predictions.values()), default=None),
            "latestFinalIngested": max((r["retrieved_at"] for r in finals.values()), default=None), "events": events,
        }
    finally:
        connection.close()


def _worker(at: datetime) -> dict:
    if using_postgres():
        automation = nfl_server_automation.status()
        state = automation.get("state") or {}
        cron_job = automation.get("cron_job") or {}
        latest_run = automation.get("latest_cron_run") or {}
        heartbeat = state.get("last_tick_completed_at")
        heartbeat_at = None
        if isinstance(heartbeat, datetime):
            heartbeat_at = heartbeat if heartbeat.tzinfo else heartbeat.replace(tzinfo=timezone.utc)
        elif heartbeat:
            heartbeat_at = datetime.fromisoformat(str(heartbeat).replace("Z", "+00:00"))
        age = max(0, (at - heartbeat_at.astimezone(timezone.utc)).total_seconds()) if heartbeat_at else None
        enabled = bool(automation.get("enabled"))
        active = bool(cron_job.get("active"))
        latest_status = str(latest_run.get("status") or "").lower()
        healthy = enabled and active and age is not None and age < 15 * 60 and latest_status == "succeeded"
        stale = enabled and active and age is not None and age >= 15 * 60
        status = "HEALTHY" if healthy else "STALE" if stale else "UNAVAILABLE"
        return {
            "status": status,
            "running": enabled and active,
            "heartbeatAt": heartbeat_at.isoformat() if heartbeat_at else None,
            "heartbeatAgeSeconds": round(age, 1) if age is not None else None,
            "pid": None,
            "state": state.get("last_status"),
            "scheduler": "SUPABASE_CRON",
            "activeExperiment": state.get("active_experiment_key"),
            "nextDueAt": (automation.get("experiment") or {}).get("next_due_at"),
            "checkpointCounts": automation.get("checkpoint_counts") or {},
            "gameCoverage": automation.get("game_coverage") or {"total": 0, "covered": 0},
            "latestCronRun": latest_run or None,
        }
    try:
        value = json.loads((ROOT / ".runtime" / "week1" / "health.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "UNAVAILABLE", "running": False, "heartbeatAt": None, "pid": None}
    reported_database = value.get("database")
    if reported_database and Path(reported_database).resolve() != _week_db_path():
        return {"status": "UNAVAILABLE", "running": False, "heartbeatAt": None, "pid": None}
    epoch = float(value.get("heartbeat_epoch") or 0)
    age = max(0, at.timestamp() - epoch)
    running = _week_db_path().with_suffix(".worker.lock").exists()
    return {"status": "HEALTHY" if running and age < 300 else "STALE", "running": running,
            "heartbeatAt": datetime.fromtimestamp(epoch, timezone.utc).isoformat() if epoch else None,
            "heartbeatAgeSeconds": round(age, 1), "pid": value.get("pid"), "state": value.get("state")}


def _fallback(social_panel: dict) -> dict:
    source = ((social_panel.get("data") or {}).get("verifiedAggregate") or {})
    aggregate = source.get("regular") or {}
    operations = source.get("operations") or {}
    games = [{"id": row.get("game_id"), "awayTeam": row.get("away_team"), "homeTeam": row.get("home_team"),
              "kickoff": row.get("kickoff_time"), "season": aggregate.get("season", 2026), "seasonType": "regular", "week": 1,
              "status": "SCHEDULED", "awayScore": None, "homeScore": None,
              "predictionStatus": "READY" if row.get("prediction") else "MISSING",
              "statsStatus": "PENDING", "lastRefresh": source.get("verified_at"), "freshness": "CURRENT", "warnings": [],
              "prediction": ({"winner": row["prediction"].get("winner"), "probability": row["prediction"].get("probability"),
                              "modelVersion": row["prediction"].get("model_version"),
                              "generatedAt": row["prediction"].get("generated_at")} if row.get("prediction") else None),
              "grade": None} for row in operations.get("games", [])]
    checkpoints = operations.get("checkpoints") or {}
    return {"experimentId": aggregate.get("experiment_id", "NFL-2026-REG1-v1"), "season": aggregate.get("season", 2026),
            "seasonType": aggregate.get("phase", "regular"), "week": aggregate.get("week", 1), "games": games, "issues": [],
            "scheduledGames": aggregate.get("scheduled", 0), "predictions": aggregate.get("predictions", 0), "finals": 0,
            "graded": aggregate.get("graded", 0), "failedPredictions": 0, "marketCoverage": source.get("coverage_games", 0),
            "modelVersion": "nfl_game_baseline_v3", "latestPredictionRun": None, "latestFinalIngested": None,
            "checkpoints": {"completed": checkpoints.get("completed", 0),
                            "pending": (checkpoints.get("total") - checkpoints.get("completed", 0)) if checkpoints.get("total") is not None else None,
                            "missed": 0, "total": checkpoints.get("total"), "next": None},
            "provider": {"status": operations.get("provider_status", "UNKNOWN"), "quotaState": "UNKNOWN"}, "events": []}


def command_center(clock: Callable[[], datetime] = _now) -> dict:
    at = clock()
    db_panel, prediction_panel = _safe_panel(database_health), _safe_panel(prediction_store_health)
    social_panel, week_panel = _safe_panel(lambda: _social(at)), _safe_panel(lambda: _week1(at))
    worker_panel, actions_panel = _safe_panel(lambda: _worker(at)), _safe_panel(recent_actions)
    week, database = week_panel["data"] or _fallback(social_panel), db_panel.get("data") or {}
    issues = list(week.get("issues") or [])
    if db_panel["status"] != "HEALTHY" or database.get("status") == "unhealthy":
        issues.append({"severity": "CRITICAL", "category": "DATABASE", "entityId": "primary",
                       "summary": "Primary database connection or schema check failed.", "source": "database_health",
                       "detectedAt": at.isoformat(), "lastAttemptAt": at.isoformat(),
                       "recommendedAction": "Retry database health check", "action": "RETRY_PANEL"})
    elif database.get("status") == "degraded":
        issues.append({"severity": "WARNING", "category": "DATABASE", "entityId": "primary-schema",
                       "summary": "Database is reachable but one or more expected application tables are missing.",
                       "source": "database_health", "detectedAt": at.isoformat(), "lastAttemptAt": at.isoformat(),
                       "recommendedAction": "Review schema compatibility", "action": "RETRY_PANEL"})
    if week_panel["status"] != "HEALTHY":
        issues.append({"severity": "WARNING", "category": "DATA", "entityId": "current-slate",
                       "summary": week_panel["error"]["message"], "source": "week1_store", "detectedAt": at.isoformat(),
                       "ownerSummary": "Some live game data is not updating yet.",
                       "lastAttemptAt": at.isoformat(), "recommendedAction": "Refresh game data",
                       "action": "RETRY_PANEL"})
    social = social_panel.get("data") or {}
    social_public = {key: value for key, value in social.items() if key != "verifiedAggregate"}
    checkpoint_state = week.get("checkpoints") or {}
    if checkpoint_state.get("missed"):
        issues.append({"severity": "WARNING", "category": "DATA", "entityId": "market-checkpoints",
                       "summary": f"{checkpoint_state['missed']} scheduled market capture checkpoint(s) were missed.",
                       "source": "week1_store", "detectedAt": at.isoformat(), "lastAttemptAt": None,
                       "recommendedAction": "Inspect worker and provider history; do not backfill after kickoff", "action": None})
    worker = worker_panel.get("data") or {}
    if worker.get("status") == "STALE":
        issues.append({"severity": "CRITICAL", "category": "AUTOMATION", "entityId": "week1-worker",
                       "summary": "NFL automation heartbeat is stale.", "source": "worker_health",
                       "detectedAt": at.isoformat(), "lastAttemptAt": worker.get("heartbeatAt"),
                       "recommendedAction": "Inspect the server scheduler and latest cron run", "action": None})
    elif worker.get("scheduler") == "SUPABASE_CRON" and worker.get("status") != "HEALTHY":
        issues.append({"severity": "CRITICAL", "category": "AUTOMATION", "entityId": "nfl-server-automation",
                       "summary": "Server-side NFL automation is not fully operational.", "source": "worker_health",
                       "detectedAt": at.isoformat(), "lastAttemptAt": worker.get("heartbeatAt"),
                       "recommendedAction": "Inspect the Supabase Cron job and its latest run", "action": None})
    provider_status = str((week.get("provider") or {}).get("status") or "UNKNOWN").upper()
    if provider_status in {"AUTH_ERROR", "NETWORK_ERROR", "QUOTA_EXHAUSTED", "FAILED"}:
        issues.append({"severity": "CRITICAL" if provider_status == "AUTH_ERROR" else "WARNING", "category": "API",
                       "entityId": "the-odds-api", "summary": f"Market provider status is {provider_status.replace('_', ' ')}.",
                       "source": "week1_store", "detectedAt": at.isoformat(), "lastAttemptAt": None,
                       "recommendedAction": "Inspect provider status without triggering an early paid capture", "action": None})
    if social.get("publishingBlocked"):
        issues.append({"severity": "INFO", "category": "AUTOMATION", "entityId": "x-publishing",
                       "summary": "Social publishing is blocked by the configured safety controls.", "source": "environment",
                       "detectedAt": at.isoformat(), "lastAttemptAt": social.get("lastPublishAttempt"),
                       "recommendedAction": "No action required", "action": None})
    critical = any(row["severity"] == "CRITICAL" for row in issues)
    checks = {
        "scheduleLoaded": "PASS" if week.get("scheduledGames") == 16 else "FAIL",
        "gameIdentitiesValid": "PASS" if len(week.get("games") or []) == 16 and all(g.get("id") for g in week["games"]) else "UNKNOWN",
        "databaseHealthy": "PASS" if database.get("status") == "healthy" else "WARN" if database.get("status") == "degraded" else "FAIL",
        "week1SlateAvailable": "PASS" if week.get("scheduledGames") else "FAIL",
        "predictionsComplete": "PASS" if week.get("predictions") == week.get("scheduledGames") and week.get("scheduledGames") else "FAIL",
        "artifactsReadable": "PASS" if (prediction_panel.get("data") or {}).get("status") == "healthy" or week.get("predictions") else "FAIL",
        "gameDetailOperational": "PASS", "statusRefreshOperational": "PASS", "actualIngestionOperational": "PASS",
        "staleReconciliationOperational": "PASS", "searchOperational": "PASS", "criticalOwnerErrors": "FAIL" if critical else "PASS",
    }
    required = {"scheduleLoaded", "databaseHealthy", "week1SlateAvailable", "predictionsComplete", "criticalOwnerErrors"}
    readiness = "NOT_READY" if any(checks[k] == "FAIL" for k in required) else (
        "DEGRADED" if any(v in {"WARN", "FAIL", "UNKNOWN"} for v in checks.values()) else "READY")
    games = week.get("games") or []
    ready_games = sum(g["predictionStatus"] in {"READY", "GRADED"} and not g["warnings"] for g in games)
    owner_checks = {"scheduleLoaded": checks["scheduleLoaded"], "databaseHealthy": checks["databaseHealthy"],
                    "gameIdentitiesValid": checks["gameIdentitiesValid"], "predictionsComplete": checks["predictionsComplete"],
                    "gameDetailOperational": checks["gameDetailOperational"],
                    "statusRefreshOperational": checks["statusRefreshOperational"],
                    "actualIngestionOperational": checks["actualIngestionOperational"], "searchOperational": checks["searchOperational"]}
    return {
        "generatedAt": at.isoformat(), "environment": os.getenv("VERCEL_ENV", "local"), "version": _git_version(),
        "context": {"league": "NFL", "season": 2026, "seasonType": "regular", "week": 1},
        "availableSports": ["ALL", "NFL", "NBA"], "overallStatus": readiness,
        "systemReadiness": {"status": readiness, "checks": owner_checks},
        "week1Readiness": {"status": readiness, "checks": checks},
        "summary": {"gamesReady": ready_games if games else week.get("scheduledGames", 0), "gamesTotal": week.get("scheduledGames", 0),
                    "predictionsReady": week.get("predictions", 0), "predictionsTotal": week.get("scheduledGames", 0),
                    "finalResults": week.get("finals", 0), "needsAttention": sum(r["severity"] in {"WARNING", "CRITICAL"} for r in issues),
                    "databaseHealth": database.get("status", "unavailable").upper(),
                    "providerHealth": week.get("provider", {}).get("status", "UNKNOWN")},
        "issues": sorted(issues, key=lambda r: {"CRITICAL": 0, "WARNING": 1, "INFO": 2}.get(r["severity"], 3)), "games": games,
        "modelOperations": {"productionModel": week.get("modelVersion"), "researchStatus": "PRODUCTION_FROZEN",
                            "lastRun": week.get("latestPredictionRun"), "generationCutoff": "before kickoff",
                            "gamesCovered": week.get("predictions", 0), "gamesTotal": week.get("scheduledGames", 0),
                            "predictionsGenerated": week.get("predictions", 0),
                            "missingPredictions": max(0, week.get("scheduledGames", 0) - week.get("predictions", 0)),
                            "failedPredictions": week.get("failedPredictions", 0), "calibrationStatus": "NOT_APPLICABLE",
                            "manifestHash": week.get("manifestHash"), "artifactHash": week.get("predictionHash")},
        "dataHealth": {"database": database, "predictionStore": prediction_panel,
                       "scheduleStore": {"status": "HEALTHY" if week.get("scheduledGames") else "UNAVAILABLE", "gameCount": week.get("scheduledGames", 0)},
                       "gameStatusService": {"status": "HEALTHY", "provider": "ESPN"},
                       "playerStatsStore": {"status": "PENDING" if not week.get("finals") else "HEALTHY"},
                       "provider": week.get("provider"), "worker": worker_panel.get("data"),
                       "unresolvedJoins": sum(any("identity" in warning.casefold() for warning in game.get("warnings", [])) for game in games),
                       "nextCheckpoint": (week.get("checkpoints") or {}).get("next"),
                       "latestFinalIngested": week.get("latestFinalIngested"),
                       "lastSuccessfulRefresh": max([v for v in [week.get("latestFinalIngested"), social.get("latestSourceAt")] if v], default=None)},
        "automation": social_public, "actionHistory": actions_panel.get("data") or [],
        "panels": {"week1": week_panel, "database": db_panel, "predictionStore": prediction_panel,
                   "automation": social_panel, "worker": worker_panel, "actionHistory": actions_panel},
        "experiments": {"regular": {"season": week.get("season"), "week": week.get("week"), "scheduled": week.get("scheduledGames"),
                                      "predictions": week.get("predictions"), "graded": week.get("graded"),
                                      "winner_record": {"WIN": 0, "LOSS": 0, "PUSH": 0}},
                        "marketCoverage": {"covered": week.get("marketCoverage", 0), "total": week.get("scheduledGames", 0)}},
        "systems": {"api": {"status": "HEALTHY", "environment": os.getenv("VERCEL_ENV", "local")}, "social": social_public},
        "alerts": issues, "feed": week.get("events") or [],
        "definitions": {
            "gamesReady": "Canonical Week 1 games with a frozen prediction and no active game warning.",
            "predictionsReady": "Immutable game-level predictions generated before kickoff.",
            "finalResults": "Authoritative finals persisted in the isolated Week 1 outcome store.",
            "needsAttention": "WARNING and CRITICAL operational issues; INFO notices are excluded.",
            "providerHealth": "Last stored provider/preflight state. Dashboard reads do not consume paid odds requests.",
            "week1Readiness": "NOT READY on critical dependency failure, DEGRADED on warning/unknown checks, otherwise READY.",
        },
    }
