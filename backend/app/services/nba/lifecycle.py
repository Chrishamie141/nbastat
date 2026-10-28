"""Durable NBA prediction, market, final-result, and settlement evidence."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from typing import Any
import uuid

from backend.app.database import column_exists, get_db_connection, using_postgres
from .grading import grade_prediction
from .season import normalize_nba_season

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dt(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def initialize() -> None:
    with get_db_connection() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS nba_games (
            game_id TEXT PRIMARY KEY, season_code TEXT NOT NULL, kickoff_time TEXT NOT NULL,
            home_team TEXT NOT NULL, away_team TEXT NOT NULL, status TEXT NOT NULL,
            home_score INTEGER, away_score INTEGER, provider TEXT NOT NULL,
            provider_event_id TEXT, provider_timestamp TEXT, retrieved_at TEXT NOT NULL,
            source_hash TEXT NOT NULL, season_phase TEXT NOT NULL DEFAULT 'unknown')""")
        if not column_exists(connection, "nba_games", "season_phase"):
            connection.execute("ALTER TABLE nba_games ADD COLUMN season_phase TEXT NOT NULL DEFAULT 'unknown'")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_nba_games_season_kickoff ON nba_games(season_code,kickoff_time,status)")
        connection.execute("""CREATE TABLE IF NOT EXISTS nba_prediction_snapshots (
            snapshot_id TEXT PRIMARY KEY, user_id BIGINT NOT NULL DEFAULT 0,
            game_id TEXT NOT NULL, season_code TEXT NOT NULL, kickoff_time TEXT NOT NULL,
            market TEXT NOT NULL, selection TEXT NOT NULL, line REAL, player_id TEXT,
            player_name TEXT, model_probability REAL, projection REAL, edge REAL,
            market_odds INTEGER, model_version TEXT NOT NULL, data_version TEXT,
            generated_at TEXT NOT NULL, feature_cutoff TEXT NOT NULL,
            source_provider TEXT, payload_json TEXT NOT NULL, snapshot_hash TEXT NOT NULL,
            created_at TEXT NOT NULL)""")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_nba_predictions_game ON nba_prediction_snapshots(season_code,game_id,generated_at)")
        connection.execute("""CREATE TABLE IF NOT EXISTS nba_market_observations (
            observation_id TEXT PRIMARY KEY, game_id TEXT NOT NULL, season_code TEXT NOT NULL,
            provider TEXT NOT NULL, sportsbook TEXT NOT NULL, market TEXT NOT NULL,
            selection TEXT NOT NULL, line REAL, odds INTEGER NOT NULL,
            observed_at TEXT NOT NULL, kickoff_time TEXT NOT NULL,
            provider_event_id TEXT, payload_json TEXT NOT NULL, source_hash TEXT NOT NULL,
            validation_status TEXT NOT NULL DEFAULT 'VALID_PREGAME', validation_reason TEXT,
            captured_at TEXT, provenance_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
            UNIQUE(game_id,provider,sportsbook,market,selection,line,odds,observed_at))""")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_nba_markets_game_time ON nba_market_observations(game_id,observed_at)")
        additions = {
            "validation_status": "TEXT NOT NULL DEFAULT 'VALID_PREGAME'",
            "validation_reason": "TEXT", "captured_at": "TEXT",
            "provenance_json": "TEXT NOT NULL DEFAULT '{}'",
        }
        for column, definition in additions.items():
            if not column_exists(connection, "nba_market_observations", column):
                connection.execute(f"ALTER TABLE nba_market_observations ADD COLUMN {column} {definition}")
        connection.execute("""CREATE TABLE IF NOT EXISTS nba_settlements (
            prediction_snapshot_id TEXT PRIMARY KEY, game_id TEXT NOT NULL,
            status TEXT NOT NULL, actual_value TEXT, reason TEXT,
            final_source_hash TEXT NOT NULL, settled_at TEXT NOT NULL,
            FOREIGN KEY(prediction_snapshot_id) REFERENCES nba_prediction_snapshots(snapshot_id))""")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_nba_settlements_game ON nba_settlements(game_id,status)")
        connection.execute("""CREATE TABLE IF NOT EXISTS nba_replay_runs (
            run_id TEXT PRIMARY KEY, season_code TEXT NOT NULL, model_version TEXT NOT NULL,
            started_at TEXT NOT NULL, completed_at TEXT, status TEXT NOT NULL,
            config_json TEXT NOT NULL, metrics_json TEXT, source_hash TEXT NOT NULL)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS nba_operational_settings (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), enabled INTEGER NOT NULL DEFAULT 1,
            season_override TEXT, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL DEFAULT 'system')""")
        connection.execute("""INSERT INTO nba_operational_settings
            (singleton,enabled,updated_at,updated_by) VALUES(1,1,?,'system')
            ON CONFLICT(singleton) DO NOTHING""", (_now(),))
        connection.execute("""CREATE TABLE IF NOT EXISTS nba_automation_runs (
            run_id TEXT PRIMARY KEY, season_code TEXT NOT NULL, started_at TEXT NOT NULL,
            completed_at TEXT, status TEXT NOT NULL, trigger_source TEXT NOT NULL,
            games_fetched INTEGER NOT NULL DEFAULT 0, games_changed INTEGER NOT NULL DEFAULT 0,
            predictions_created INTEGER NOT NULL DEFAULT 0, predictions_settled INTEGER NOT NULL DEFAULT 0,
            predictions_pending INTEGER NOT NULL DEFAULT 0, error_code TEXT, details_json TEXT NOT NULL DEFAULT '{}')""")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_nba_automation_runs_started ON nba_automation_runs(started_at,status)")
        if not using_postgres():
            for table in ("nba_prediction_snapshots", "nba_market_observations"):
                connection.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_immutable_update
                    BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT,'immutable NBA evidence'); END""")
                connection.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_immutable_delete
                    BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT,'immutable NBA evidence'); END""")
        if using_postgres():
            for table in ("nba_games", "nba_prediction_snapshots", "nba_market_observations", "nba_settlements", "nba_replay_runs",
                          "nba_operational_settings", "nba_automation_runs"):
                connection.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
                connection.execute(f"REVOKE ALL ON TABLE {table} FROM anon, authenticated")
    from .registry import initialize_registry
    initialize_registry()


def upsert_game(game: dict) -> bool:
    initialize()
    season = normalize_nba_season(game["season"])
    payload = {
        "game_id": str(game["game_id"]), "season": season.code,
        "kickoff_time": str(game["kickoff_time"]), "home_team": str(game["home_team"]),
        "away_team": str(game["away_team"]), "status": str(game.get("status") or "SCHEDULED").upper(),
        "home_score": game.get("home_score"), "away_score": game.get("away_score"),
        "provider": str(game.get("provider") or "unknown"),
        "provider_event_id": game.get("provider_event_id"),
        "provider_timestamp": game.get("provider_timestamp"),
        "season_phase": str(game.get("season_phase") or "unknown"),
    }
    with get_db_connection() as connection:
        existing = connection.execute("SELECT * FROM nba_games WHERE game_id=?", (payload["game_id"],)).fetchone()
        if existing and str(existing["status"]).upper() == "FINAL" and payload["status"] != "FINAL":
            payload["status"] = "FINAL"
            payload["home_score"] = existing["home_score"]
            payload["away_score"] = existing["away_score"]
        source_hash = _hash(payload)
        if existing and existing["source_hash"] == source_hash:
            return False
        connection.execute("""INSERT INTO nba_games
            (game_id,season_code,kickoff_time,home_team,away_team,status,home_score,away_score,
             provider,provider_event_id,provider_timestamp,retrieved_at,source_hash,season_phase)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(game_id) DO UPDATE SET season_code=excluded.season_code,
              kickoff_time=excluded.kickoff_time,home_team=excluded.home_team,away_team=excluded.away_team,
              status=excluded.status,home_score=excluded.home_score,away_score=excluded.away_score,
              provider=excluded.provider,provider_event_id=excluded.provider_event_id,
              provider_timestamp=excluded.provider_timestamp,retrieved_at=excluded.retrieved_at,
              source_hash=excluded.source_hash,season_phase=excluded.season_phase""",
            (payload["game_id"], season.code, payload["kickoff_time"], payload["home_team"], payload["away_team"],
             payload["status"], payload["home_score"], payload["away_score"], payload["provider"],
             payload["provider_event_id"], payload["provider_timestamp"], _now(), source_hash,
             payload["season_phase"]))
    return True


def freeze_prediction(prediction: dict, *, user_id: int = 0) -> str:
    initialize()
    generated_at = str(prediction["generated_at"])
    kickoff = str(prediction["kickoff_time"])
    feature_cutoff = str(prediction.get("feature_cutoff") or generated_at)
    if _dt(generated_at) >= _dt(kickoff) or _dt(feature_cutoff) > _dt(generated_at):
        raise ValueError("NBA predictions must be generated from features frozen strictly before kickoff")
    model_version = str(prediction.get("model_version") or "")
    if not model_version.startswith("nba-"):
        raise ValueError("NBA snapshots require an NBA-owned model version")
    season = normalize_nba_season(prediction["season"])
    payload = {**prediction, "season": season.code, "user_id": int(user_id)}
    snapshot_hash = _hash(payload)
    snapshot_id = f"nba-pred-{snapshot_hash[:32]}"
    values = (
        snapshot_id, int(user_id), str(prediction["game_id"]), season.code, kickoff,
        str(prediction["market"]).lower(), str(prediction["selection"]), prediction.get("line"),
        prediction.get("player_id"), prediction.get("player_name"), prediction.get("model_probability"),
        prediction.get("projection"), prediction.get("edge"), prediction.get("market_odds"),
        model_version, prediction.get("data_version"), generated_at, feature_cutoff,
        prediction.get("source_provider"), _json(payload), snapshot_hash, _now(),
    )
    with get_db_connection() as connection:
        connection.execute("""INSERT INTO nba_prediction_snapshots
            (snapshot_id,user_id,game_id,season_code,kickoff_time,market,selection,line,player_id,
             player_name,model_probability,projection,edge,market_odds,model_version,data_version,
             generated_at,feature_cutoff,source_provider,payload_json,snapshot_hash,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(snapshot_id) DO NOTHING""", values)
    logger.info("nba_prediction_frozen %s", _json({"snapshotId": snapshot_id, "gameId": prediction["game_id"], "modelVersion": model_version}))
    return snapshot_id


def append_market_observation(observation: dict) -> str:
    initialize()
    observed_at, kickoff = str(observation["observed_at"]), str(observation["kickoff_time"])
    if _dt(observed_at) >= _dt(kickoff):
        raise ValueError("NBA market observations must be strictly before kickoff")
    if not str(observation.get("provider") or "").strip() or not str(observation.get("sportsbook") or "").strip():
        raise ValueError("NBA market observations require provider and sportsbook provenance")
    try:
        odds = int(observation["odds"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("NBA market observations require valid American odds") from exc
    if odds == 0:
        raise ValueError("NBA market observations require non-zero American odds")
    season = normalize_nba_season(observation["season"])
    payload = {**observation, "season": season.code}
    source_hash = _hash(payload)
    observation_id = f"nba-market-{source_hash[:32]}"
    with get_db_connection() as connection:
        connection.execute("""INSERT INTO nba_market_observations
            (observation_id,game_id,season_code,provider,sportsbook,market,selection,line,odds,
             observed_at,kickoff_time,provider_event_id,payload_json,source_hash,validation_status,
             validation_reason,captured_at,provenance_json,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(observation_id) DO NOTHING""",
            (observation_id, str(observation["game_id"]), season.code, str(observation["provider"]),
             str(observation["sportsbook"]), str(observation["market"]).lower(), str(observation["selection"]),
             observation.get("line"), odds, observed_at, kickoff,
             observation.get("provider_event_id"), _json(payload), source_hash, "VALID_PREGAME",
             None, _now(), _json(observation.get("provenance") or {
                 "provider": observation["provider"], "sportsbook": observation["sportsbook"],
             }), _now()))
    return observation_id


def settle_game(game_id: str, *, player_stats: dict | None = None) -> dict:
    initialize()
    settled = pending = already = 0
    with get_db_connection() as connection:
        game_row = connection.execute("SELECT * FROM nba_games WHERE game_id=?", (game_id,)).fetchone()
        if not game_row:
            return {"settled": 0, "pending": 0, "alreadySettled": 0, "reason": "game_not_found"}
        game = dict(game_row)
        game["player_stats"] = player_stats or {}
        predictions = connection.execute("""SELECT p.* FROM nba_prediction_snapshots p
            WHERE p.game_id=? ORDER BY p.generated_at,p.snapshot_id""", (game_id,)).fetchall()
        for raw in predictions:
            prediction = dict(raw)
            existing = connection.execute("SELECT status FROM nba_settlements WHERE prediction_snapshot_id=?", (prediction["snapshot_id"],)).fetchone()
            if existing and existing["status"] != "UNRESOLVED":
                already += 1
                continue
            result = grade_prediction(prediction, game)
            if result["status"] == "UNRESOLVED":
                pending += 1
            connection.execute("""INSERT INTO nba_settlements
                (prediction_snapshot_id,game_id,status,actual_value,reason,final_source_hash,settled_at)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(prediction_snapshot_id) DO NOTHING""",
                (prediction["snapshot_id"], game_id, result["status"],
                 None if result["actual"] is None else str(result["actual"]), result["reason"],
                 game["source_hash"], _now()))
            if existing and existing["status"] == "UNRESOLVED" and result["status"] != "UNRESOLVED":
                connection.execute("""UPDATE nba_settlements SET status=?,actual_value=?,reason=?,
                    final_source_hash=?,settled_at=? WHERE prediction_snapshot_id=? AND status='UNRESOLVED'""",
                    (result["status"], None if result["actual"] is None else str(result["actual"]),
                     result["reason"], game["source_hash"], _now(), prediction["snapshot_id"]))
            if result["status"] != "UNRESOLVED":
                settled += 1
    logger.info("nba_game_settlement %s", _json({"gameId": game_id, "settled": settled, "pending": pending, "already": already}))
    return {"settled": settled, "pending": pending, "alreadySettled": already}


def reconcile_persisted_finals(*, season: str | int) -> dict:
    """Settle every eligible immutable snapshot whose stored game is final.

    This recovery sweep is independent of the provider response window. It
    closes the gap left by an interrupted cycle after final scores were stored
    but before every snapshot was graded. Existing terminal settlements remain
    immutable because :func:`settle_game` skips them.
    """
    initialize()
    season_code = normalize_nba_season(season).code
    with get_db_connection() as connection:
        rows = connection.execute(
            """SELECT DISTINCT p.game_id
               FROM nba_prediction_snapshots p
               JOIN nba_games g ON g.game_id=p.game_id
               LEFT JOIN nba_settlements s ON s.prediction_snapshot_id=p.snapshot_id
               WHERE p.season_code=? AND g.status='FINAL'
                 AND p.generated_at < p.kickoff_time
                 AND p.feature_cutoff <= p.generated_at
                 AND (s.prediction_snapshot_id IS NULL OR s.status='UNRESOLVED')
               ORDER BY p.game_id""",
            (season_code,),
        ).fetchall()
    report = {"games": 0, "settled": 0, "pending": 0, "alreadySettled": 0}
    for row in rows:
        result = settle_game(str(row["game_id"]))
        report["games"] += 1
        report["settled"] += int(result.get("settled") or 0)
        report["pending"] += int(result.get("pending") or 0)
        report["alreadySettled"] += int(result.get("alreadySettled") or 0)
    return report


def unresolved_past_predictions(*, season: str | int, at: datetime | None = None) -> int:
    """Count past snapshots that still need a verified result or settlement."""
    initialize()
    season_code = normalize_nba_season(season).code
    cutoff = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff_text = cutoff.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    with get_db_connection() as connection:
        row = connection.execute(
            """SELECT COUNT(*) AS count
               FROM nba_prediction_snapshots p
               LEFT JOIN nba_settlements s ON s.prediction_snapshot_id=p.snapshot_id
               WHERE p.season_code=? AND p.kickoff_time < ?
                 AND p.generated_at < p.kickoff_time
                 AND p.feature_cutoff <= p.generated_at
                 AND (s.prediction_snapshot_id IS NULL OR s.status='UNRESOLVED')""",
            (season_code, cutoff_text),
        ).fetchone()
    return int(row["count"])


def persist_replay_summary(summary: dict, *, config: dict) -> str:
    """Persist one immutable replay result and update research evidence."""
    initialize()
    payload = {"summary": summary, "config": config}
    source_hash = _hash(payload)
    run_id = f"nba-replay-{source_hash[:32]}"
    with get_db_connection() as connection:
        connection.execute("""INSERT INTO nba_replay_runs
            (run_id,season_code,model_version,started_at,completed_at,status,
             config_json,metrics_json,source_hash) VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(run_id) DO NOTHING""",
            (run_id, normalize_nba_season(summary["season"]).code, summary["modelVersion"],
             _now(), _now(), "COMPLETE", _json(config), _json(summary["metrics"]), source_hash))
    from .registry import record_evaluation
    periods = sorted((summary.get("metrics", {}).get("byTimePeriod") or {}).keys())
    record_evaluation(model_version=summary["modelVersion"], metrics=summary["metrics"],
                      evaluation_start=periods[0] if periods else None,
                      evaluation_end=periods[-1] if periods else None)
    return run_id


def operational_settings() -> dict:
    initialize()
    with get_db_connection() as connection:
        row = connection.execute("SELECT * FROM nba_operational_settings WHERE singleton=1").fetchone()
    env_enabled = os.getenv("NBA_AUTOMATION_ENABLED")
    enabled = bool(row["enabled"])
    source = "database"
    if env_enabled not in (None, ""):
        enabled = env_enabled.strip().casefold() in {"1", "true", "yes", "on"}
        source = "environment"
    season_override = str(row["season_override"] or "").strip() or str(os.getenv("NBA_ACTIVE_SEASON") or "").strip() or None
    return {"enabled": enabled, "seasonOverride": season_override, "source": source,
            "updatedAt": row["updated_at"], "updatedBy": row["updated_by"]}


def update_operational_settings(*, enabled: bool | None = None, season_override: str | None = None,
                                actor: str = "system") -> dict:
    initialize()
    normalized = normalize_nba_season(season_override).code if season_override else None
    with get_db_connection() as connection:
        current = connection.execute("SELECT * FROM nba_operational_settings WHERE singleton=1").fetchone()
        next_enabled = int(bool(enabled)) if enabled is not None else int(current["enabled"])
        connection.execute("""UPDATE nba_operational_settings SET enabled=?,season_override=?,updated_at=?,updated_by=?
            WHERE singleton=1""", (next_enabled, normalized, _now(), str(actor)))
    return operational_settings()


def start_automation_run(*, season: str | int, trigger_source: str) -> str:
    initialize()
    run_id = f"nba-auto-{uuid.uuid4().hex}"
    with get_db_connection() as connection:
        connection.execute("""INSERT INTO nba_automation_runs
            (run_id,season_code,started_at,status,trigger_source,details_json)
            VALUES (?,?,?,'RUNNING',?,'{}')""",
            (run_id, normalize_nba_season(season).code, _now(), str(trigger_source)))
    return run_id


def finish_automation_run(run_id: str, *, status: str, report: dict | None = None,
                          error_code: str | None = None) -> None:
    details = report or {}
    with get_db_connection() as connection:
        connection.execute("""UPDATE nba_automation_runs SET completed_at=?,status=?,games_fetched=?,
            games_changed=?,predictions_created=?,predictions_settled=?,predictions_pending=?,error_code=?,details_json=?
            WHERE run_id=? AND status='RUNNING'""", (
                _now(), str(status), int(details.get("gamesFetched") or 0), int(details.get("gamesChanged") or 0),
                int(details.get("predictionsCreated") or 0), int(details.get("predictionsSettled") or 0),
                int(details.get("predictionsPendingData") or 0), error_code, _json(details), run_id,
            ))


def automation_status() -> dict:
    initialize()
    with get_db_connection() as connection:
        latest = connection.execute("SELECT * FROM nba_automation_runs ORDER BY started_at DESC LIMIT 1").fetchone()
        failed = connection.execute("SELECT COUNT(*) AS count FROM nba_automation_runs WHERE status='FAILED'").fetchone()
        last_failure = connection.execute(
            "SELECT * FROM nba_automation_runs WHERE status='FAILED' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        recent = connection.execute(
            "SELECT status FROM nba_automation_runs ORDER BY started_at DESC LIMIT 25"
        ).fetchall()
    settings = operational_settings()
    latest_row = dict(latest) if latest else None
    consecutive_failures = 0
    for row in recent:
        if str(row["status"]).upper() != "FAILED":
            break
        consecutive_failures += 1
    age_hours = None
    if latest_row:
        completed = latest_row.get("completed_at") or latest_row.get("started_at")
        if completed:
            age_hours = round(max(0.0, (datetime.now(timezone.utc) - _dt(completed)).total_seconds()) / 3600, 2)
    configured = bool(os.getenv("CRON_SECRET") or os.getenv("NBA_AUTOMATION_SECRET"))
    stale = bool(settings["enabled"] and latest_row and age_hours is not None and age_hours > 27)
    if not settings["enabled"]:
        monitor_status = "PAUSED"
    elif not configured:
        monitor_status = "UNCONFIGURED"
    elif consecutive_failures:
        monitor_status = "FAILED"
    elif stale:
        monitor_status = "STALE"
    elif latest_row:
        monitor_status = "HEALTHY"
    else:
        monitor_status = "AWAITING_FIRST_RUN"
    return {"settings": settings, "latestRun": dict(latest) if latest else None,
            "failedRuns": int(failed["count"]), "lastFailure": dict(last_failure) if last_failure else None,
            "consecutiveFailures": consecutive_failures, "latestRunAgeHours": age_hours,
            "stale": stale, "monitorStatus": monitor_status,
            "scheduler": "VERCEL_CRON", "configured": configured,
            "cadence": {"schedule": "17 10 * * *", "frequency": "DAILY", "timezone": "UTC",
                        "expectedRunTime": "10:17", "staleAfterHours": 27,
                        "scheduleIngestionHorizonDays": 14},
            "failureMonitoring": {"persistentRunLedger": True, "commandCenterAlerts": True,
                                  "automaticRetry": False, "externalAlerting": False}}


def foundation_status(season: str | int) -> dict:
    initialize()
    code = normalize_nba_season(season).code
    with get_db_connection() as connection:
        counts = {}
        for key, table in (("games", "nba_games"), ("predictions", "nba_prediction_snapshots"),
                           ("markets", "nba_market_observations"), ("settlements", "nba_settlements"),
                           ("replayRuns", "nba_replay_runs")):
            column = "season_code" if table != "nba_settlements" else None
            if column:
                row = connection.execute(f"SELECT COUNT(*) AS count FROM {table} WHERE {column}=?", (code,)).fetchone()
            else:
                row = connection.execute("""SELECT COUNT(*) AS count FROM nba_settlements s
                    JOIN nba_prediction_snapshots p ON p.snapshot_id=s.prediction_snapshot_id
                    WHERE p.season_code=?""", (code,)).fetchone()
            counts[key] = int(row["count"])
        prediction_models = {
            str(row["model_version"]): int(row["count"])
            for row in connection.execute(
                """SELECT model_version,COUNT(*) AS count FROM nba_prediction_snapshots
                   WHERE season_code=? GROUP BY model_version ORDER BY model_version""",
                (code,),
            ).fetchall()
        }
    from .availability import personnel_health
    from .registry import registry_status
    personnel = personnel_health(code)
    return {"league": "NBA", "season": code, "modelIsolation": "NBA_ONLY",
            "productionReady": False, "modelRegistry": registry_status(),
            "automation": automation_status(),
            "capabilities": {"schedule": "OPERATIONAL", "teamData": "GAME_FEED",
                             "playerData": "ON_DEMAND_NBA_STATS_WITH_EXPLICIT_HEALTH",
                             "rosters": personnel["roster"]["status"],
                             "injuries": personnel["availability"]["status"]},
            "personnel": personnel, "predictionModels": prediction_models, **counts}
