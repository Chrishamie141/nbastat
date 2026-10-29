"""Durable, idempotent current-season NFL history and forecast refresh.

This service changes data availability only.  It calls the existing NFL V2
champion and never mutates prior history, forecast snapshots, evaluated
predictions, settlements, or grades.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from backend.app.database import (
    database_url,
    get_db_connection_for_url,
    is_postgres_url,
    table_exists,
)

logger = logging.getLogger(__name__)
PIPELINE_LOCK_KEY = 748219411
DEPLOYMENT_ENVIRONMENTS = {"staging", "production"}
PRODUCTION_JOB_NAME = "smartbets-nfl-data-refresh-daily-production"
PRODUCTION_CRON_SCHEDULE = "0 7,8 * * *"
PRODUCTION_TIME_ZONE = "America/New_York"
PRODUCTION_LOCAL_TIME = "03:00"
STAGING_JOB_NAME = "smartbets-nfl-data-refresh-30m-staging"
STAGING_CRON_SCHEDULE = "*/30 * * * *"


class HistoryConflict(RuntimeError):
    pass


def _now(clock=None) -> datetime:
    value = clock() if clock else datetime.now(timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _row_value(row, key, default=None):
    if row is None:
        return default
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def _enabled() -> bool:
    return os.getenv("NFL_DATA_REFRESH_ENABLED", "false").strip().lower() == "true"


def _install_enabled() -> bool:
    return os.getenv("NFL_DATA_REFRESH_INSTALL_ENABLED", "false").strip().lower() == "true"


def _pipeline_database_url() -> str:
    return (os.getenv("NFL_DATA_REFRESH_DATABASE_URL") or "").strip() or database_url()


def _pipeline_using_postgres() -> bool:
    return is_postgres_url(_pipeline_database_url())


def _pipeline_connection():
    return get_db_connection_for_url(_pipeline_database_url())


def _deployment_environment() -> str:
    explicit = os.getenv("NFL_DATA_REFRESH_ENVIRONMENT", "").strip().lower()
    if explicit:
        if explicit not in DEPLOYMENT_ENVIRONMENTS:
            raise RuntimeError("NFL_DATA_REFRESH_ENVIRONMENT_INVALID")
        return explicit
    vercel_environment = os.getenv("VERCEL_ENV", "").strip().lower()
    if vercel_environment == "production":
        return "production"
    if vercel_environment in {"preview", "development"}:
        return "staging"
    return "local"


def _job_name(environment: str | None = None) -> str:
    selected = environment or _deployment_environment()
    if selected not in DEPLOYMENT_ENVIRONMENTS:
        raise RuntimeError("NFL_DATA_REFRESH_ENVIRONMENT_REQUIRED")
    return PRODUCTION_JOB_NAME if selected == "production" else STAGING_JOB_NAME


def _expected_schedule(environment: str | None = None) -> str:
    selected = environment or _deployment_environment()
    if selected not in DEPLOYMENT_ENVIRONMENTS:
        raise RuntimeError("NFL_DATA_REFRESH_ENVIRONMENT_REQUIRED")
    return (PRODUCTION_CRON_SCHEDULE if selected == "production"
            else STAGING_CRON_SCHEDULE)


def _acquire_pipeline_lock():
    """Hold a dedicated PostgreSQL session lock for the complete cloud run."""
    if not _pipeline_using_postgres():
        return None, True
    connection = _pipeline_connection()
    try:
        row = connection.execute(
            f"SELECT pg_try_advisory_lock({PIPELINE_LOCK_KEY}) AS locked"
        ).fetchone()
        acquired = bool(_row_value(row, "locked", False))
        connection.commit()
        if not acquired:
            connection.close()
            return None, False
        return connection, True
    except Exception:
        connection.close()
        raise


def _release_pipeline_lock(connection) -> None:
    if connection is None:
        return
    try:
        connection.execute(
            f"SELECT pg_advisory_unlock({PIPELINE_LOCK_KEY}) AS unlocked"
        )
        connection.commit()
    finally:
        connection.close()


def initialize() -> None:
    """Provide the additive schema for isolated SQLite tests and local review."""
    if _pipeline_using_postgres():
        required = ("nfl_team_game_history", "nfl_data_pipeline_runs",
                    "nfl_data_pipeline_state", "nfl_forecast_snapshots",
                    "nfl_game_predictions")
        with _pipeline_connection() as connection:
            missing = [name for name in required if not table_exists(
                connection, name, postgres=True
            )]
        if missing:
            raise RuntimeError("NFL current-season migration is not installed")
        return
    with _pipeline_connection() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS nfl_team_game_history(
            history_key TEXT PRIMARY KEY,season INTEGER NOT NULL,season_type TEXT NOT NULL,
            week INTEGER NOT NULL,game_id TEXT NOT NULL,team TEXT NOT NULL,opponent TEXT NOT NULL,
            home_away TEXT NOT NULL,points_for INTEGER NOT NULL,points_against INTEGER NOT NULL,
            kickoff_time TEXT NOT NULL,completed_at TEXT NOT NULL,source_completed_at TEXT,
            first_observed_final_at TEXT NOT NULL,last_verified_at TEXT NOT NULL,ingested_at TEXT NOT NULL,
            data_as_of TEXT NOT NULL,provider TEXT NOT NULL,provider_status TEXT NOT NULL,
            source_hash TEXT NOT NULL,provenance_json TEXT NOT NULL,
            record_role TEXT NOT NULL DEFAULT 'completed_game_history',is_pregame INTEGER NOT NULL DEFAULT 0,
            UNIQUE(season,season_type,game_id,team))""")
        connection.execute("""CREATE TABLE IF NOT EXISTS nfl_data_pipeline_runs(
            run_id TEXT PRIMARY KEY,trigger_source TEXT NOT NULL,season INTEGER NOT NULL,
            started_at TEXT NOT NULL,completed_at TEXT,status TEXT NOT NULL,safe_error_code TEXT,
            weeks_checked INTEGER NOT NULL DEFAULT 0,games_observed INTEGER NOT NULL DEFAULT 0,
            history_rows_inserted INTEGER NOT NULL DEFAULT 0,duplicate_rows INTEGER NOT NULL DEFAULT 0,
            conflict_rows INTEGER NOT NULL DEFAULT 0,forecasts_created INTEGER NOT NULL DEFAULT 0,
            forecasts_unchanged INTEGER NOT NULL DEFAULT 0,officials_frozen INTEGER NOT NULL DEFAULT 0,
            latest_history_cutoff TEXT,details_json TEXT NOT NULL DEFAULT '{}')""")
        connection.execute("""CREATE TABLE IF NOT EXISTS nfl_data_pipeline_state(
            singleton INTEGER PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 1,last_started_at TEXT,
            last_success_at TEXT,last_status TEXT NOT NULL DEFAULT 'BOOTSTRAP',last_error_code TEXT,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,last_season INTEGER,last_week INTEGER,
            latest_game_id TEXT,latest_history_cutoff TEXT,last_forecast_refresh_at TEXT,updated_at TEXT NOT NULL)""")
        connection.execute("""CREATE TABLE IF NOT EXISTS nfl_forecast_snapshots(
            forecast_id TEXT PRIMARY KEY,run_id TEXT,game_id TEXT NOT NULL,season INTEGER NOT NULL,
            season_type TEXT NOT NULL,display_week INTEGER NOT NULL,kickoff_time TEXT NOT NULL,
            generated_at TEXT NOT NULL,model_version TEXT NOT NULL,history_cutoff TEXT NOT NULL,
            history_hash TEXT NOT NULL,history_row_count INTEGER NOT NULL,prediction_hash TEXT NOT NULL,
            prediction_json TEXT NOT NULL,official_prediction_id INTEGER,
            UNIQUE(game_id,model_version,history_hash,prediction_hash))""")
        connection.execute("""INSERT INTO nfl_data_pipeline_state(singleton,enabled,last_status,updated_at)
            VALUES(1,1,'BOOTSTRAP',?) ON CONFLICT(singleton) DO NOTHING""", (_iso(_now()),))
    from backend.app.services.nfl_product_service import _initialize_predictions_for_database
    _initialize_predictions_for_database(_pipeline_database_url())


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _normalize_final(game: dict, week: int, observed_at: datetime) -> list[dict]:
    required = ("game_id", "kickoff_time", "home_team", "away_team", "home_score", "away_score")
    if game.get("status") != "final" or any(game.get(key) is None for key in required):
        raise ValueError("FINAL_GAME_INCOMPLETE")
    home, away = str(game["home_team"]).strip().upper(), str(game["away_team"]).strip().upper()
    if not home or not away or home == away:
        raise ValueError("FINAL_GAME_TEAMS_INVALID")
    try:
        home_score, away_score = int(game["home_score"]), int(game["away_score"])
        kickoff = _parse_time(game["kickoff_time"])
    except (TypeError, ValueError) as exc:
        raise ValueError("FINAL_GAME_VALUES_INVALID") from exc
    if home_score < 0 or away_score < 0 or kickoff > observed_at:
        raise ValueError("FINAL_GAME_VALUES_INVALID")
    # ESPN's public scoreboard does not expose the instant the status changed
    # to final.  Six hours after kickoff is a documented conservative
    # availability boundary; the factual first observation is retained too.
    completed = kickoff + timedelta(hours=6)
    core = {
        "season": int(game["season"]), "season_type": "regular", "week": int(week),
        "game_id": str(game["game_id"]), "kickoff_time": _iso(kickoff),
        "home_team": home, "away_team": away, "home_score": home_score,
        "away_score": away_score, "provider": "espn-scoreboard", "provider_status": "final",
    }
    source_hash = _digest(core)
    rows = []
    for team, opponent, venue, points_for, points_against in (
        (home, away, "home", home_score, away_score),
        (away, home, "away", away_score, home_score),
    ):
        history_key = _digest({"season": core["season"], "season_type": "regular",
                               "game_id": core["game_id"], "team": team})
        rows.append({
            "history_key": history_key, "season": core["season"], "season_type": "regular",
            "week": int(week), "game_id": core["game_id"], "team": team, "opponent": opponent,
            "home_away": venue, "points_for": points_for, "points_against": points_against,
            "kickoff_time": core["kickoff_time"], "completed_at": _iso(completed),
            "source_completed_at": None, "first_observed_final_at": _iso(observed_at),
            "last_verified_at": _iso(observed_at), "ingested_at": _iso(observed_at),
            "data_as_of": _iso(completed), "provider": core["provider"],
            "provider_status": "final", "source_hash": source_hash,
            "provenance_json": _canonical({**core, "sourceCompletedAt": None,
                "availabilityBoundary": "kickoff_plus_6h", "firstObservedFinalAt": _iso(observed_at)}),
            "record_role": "completed_game_history", "is_pregame": 0,
        })
    return rows


def _load_week(schedule_loader, season: int, week: int, attempts: int = 3) -> list[dict]:
    error = None
    for _attempt in range(attempts):
        try:
            return schedule_loader(season, week, "regular")
        except Exception as exc:
            error = exc
            logger.warning("nfl_history_provider_retry week=%s code=%s", week, type(exc).__name__)
    raise RuntimeError("NFL_HISTORY_PROVIDER_UNAVAILABLE") from error


def _history_cutoff(connection) -> tuple[str | None, str | None]:
    row = connection.execute("""SELECT game_id,completed_at FROM nfl_team_game_history
        ORDER BY completed_at DESC,game_id DESC LIMIT 1""").fetchone()
    return (_row_value(row, "completed_at"), _row_value(row, "game_id"))


def _upsert_history(connection, rows: list[dict]) -> tuple[int, int, int]:
    inserted = duplicates = conflicts = 0
    columns = tuple(rows[0].keys()) if rows else ()
    for row in rows:
        existing = connection.execute("SELECT source_hash FROM nfl_team_game_history WHERE history_key=?",
                                      (row["history_key"],)).fetchone()
        if existing:
            if existing["source_hash"] == row["source_hash"]:
                duplicates += 1
                continue
            conflicts += 1
            raise HistoryConflict("NFL_HISTORY_CONFLICT")
        placeholders = ",".join("?" for _ in columns)
        connection.execute(
            f"INSERT INTO nfl_team_game_history({','.join(columns)}) VALUES({placeholders})",
            tuple(row[column] for column in columns),
        )
        inserted += 1
    return inserted, duplicates, conflicts


def _history_for_kickoff(all_history: list[dict], kickoff: str) -> list[dict]:
    boundary = _parse_time(kickoff)
    result = []
    for row in all_history:
        completed = row.get("completed_at")
        if completed and _parse_time(completed) < boundary:
            result.append(row)
    return result


def _refresh_forecasts(connection, *, run_id: str, season: int, weeks: list[int],
                       schedule_loader, generated_at: datetime) -> tuple[int, int, int]:
    from backend.app.services.nfl_product_service import (
        HISTORY_PATH, _save_prediction_with_connection,
        build_regular_season_prediction,
    )
    if not table_exists(connection, "nfl_game_predictions",
                        postgres=_pipeline_using_postgres()):
        raise RuntimeError("NFL_PREDICTION_STORAGE_REQUIRED")
    history = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))["items"]
    current_rows = connection.execute("""SELECT season,week,game_id,team,opponent,home_away,
        points_for,points_against,completed_at,data_as_of,record_role,is_pregame,provider
        FROM nfl_team_game_history ORDER BY completed_at,game_id,team""").fetchall()
    history.extend(dict(row) for row in current_rows)
    created = unchanged = frozen = 0
    for week in sorted(set(weeks)):
        for raw in _load_week(schedule_loader, season, week):
            game = {**raw, "week": week, "display_week": week, "provider_week": week,
                    "season_type": "regular", "provider": "espn", "week_key": f"REG{week}",
                    "week_label": f"Week {week}"}
            if game.get("status") != "scheduled" or _parse_time(game["kickoff_time"]) <= generated_at:
                continue
            eligible_history = _history_for_kickoff(history, game["kickoff_time"])
            prediction = build_regular_season_prediction(game, eligible_history)
            if prediction is None:
                raise RuntimeError("NFL_FORECAST_HISTORY_INSUFFICIENT")
            history_rows = [{key: row.get(key) for key in (
                "season", "week", "game_id", "team", "opponent", "points_for",
                "points_against", "home_away", "completed_at")}
                for row in eligible_history]
            history_hash = _digest(history_rows)
            prediction_json = _canonical(prediction)
            prediction_hash = hashlib.sha256(prediction_json.encode("utf-8")).hexdigest()
            history_cutoff = max(str(row["completed_at"]) for row in eligible_history)
            forecast_id = _digest({"game": game["game_id"], "model": prediction["modelVersion"],
                                   "history": history_hash, "prediction": prediction_hash})
            cursor = connection.execute("""INSERT INTO nfl_forecast_snapshots(
                forecast_id,run_id,game_id,season,season_type,display_week,kickoff_time,generated_at,
                model_version,history_cutoff,history_hash,history_row_count,prediction_hash,prediction_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(game_id,model_version,history_hash,prediction_hash) DO NOTHING""",
                (forecast_id, run_id, game["game_id"], season, "regular", week,
                 game["kickoff_time"], _iso(generated_at), prediction["modelVersion"],
                 history_cutoff, history_hash, len(eligible_history), prediction_hash, prediction_json))
            if cursor.rowcount:
                created += 1
            else:
                unchanged += 1
            official = connection.execute("""SELECT id FROM nfl_game_predictions
                WHERE user_id=0 AND game_id=? AND model_version=? LIMIT 1""",
                (game["game_id"], prediction["modelVersion"])).fetchone()
            if not official:
                _save_prediction_with_connection(connection, 0, game, prediction)
                official = connection.execute("""SELECT id FROM nfl_game_predictions
                    WHERE user_id=0 AND game_id=? AND model_version=? LIMIT 1""",
                    (game["game_id"], prediction["modelVersion"])).fetchone()
                if official:
                    frozen += 1
            # Forecast rows are immutable; linking is optional evidence and is
            # included only at creation time by the schema's nullable column.
    return created, unchanged, frozen


def run(*, season: int | None = None, trigger_source: str = "supabase_cron",
        schedule_loader=None, context_resolver=None, clock=None) -> dict:
    """Run one bounded catch-up and forecast cycle."""
    run_id = str(uuid.uuid4())
    if not _enabled():
        return {"runId": run_id, "status": "DISABLED", "networkContacted": False}
    from backend.app.services.nfl_product_service import (
        _schedule, current_week_context, nfl_season_year, planning_week_context,
    )
    initialize()
    now = _now(clock)
    season = int(season or nfl_season_year(now))
    schedule_loader = schedule_loader or _schedule
    context_resolver = context_resolver or current_week_context
    lock_connection, lock_acquired = _acquire_pipeline_lock()
    if not lock_acquired:
        return {"runId": run_id, "status": "ALREADY_RUNNING", "networkContacted": False}
    try:
        with _pipeline_connection() as connection:
            connection.execute("""INSERT INTO nfl_data_pipeline_runs(
                run_id,trigger_source,season,started_at,status,details_json) VALUES(?,?,?,?,?,?)""",
                (run_id, trigger_source, season, _iso(now), "RUNNING", "{}"))
            connection.execute("""UPDATE nfl_data_pipeline_state SET last_started_at=?,last_status='RUNNING',
                last_error_code=NULL,updated_at=? WHERE singleton=1""", (_iso(now), _iso(now)))
        context = context_resolver(season, at=now)
        if str(context.get("seasonType")) != "regular":
            raise RuntimeError("NFL_REGULAR_SEASON_INACTIVE")
        active_week = int(context["week"])
        with _pipeline_connection() as connection:
            found = {int(row["week"]) for row in connection.execute(
                "SELECT DISTINCT week FROM nfl_team_game_history WHERE season=? AND season_type='regular'",
                (season,)).fetchall()}
        missing = [week for week in range(1, active_week + 1) if week not in found]
        weeks = sorted(set(missing + list(range(max(1, active_week - 1), active_week + 1))))
        games, normalized = 0, []
        for week in weeks:
            schedule = _load_week(schedule_loader, season, week)
            if not schedule:
                raise RuntimeError("NFL_SCHEDULE_WEEK_EMPTY")
            for game in schedule:
                if game.get("status") == "final":
                    games += 1
                    normalized.extend(_normalize_final(game, week, now))
                elif game.get("kickoff_time") and _parse_time(game["kickoff_time"]) + timedelta(hours=8) < now:
                    raise RuntimeError("NFL_EXPECTED_FINAL_MISSING")
        with _pipeline_connection() as connection:
            inserted, duplicates, conflicts = _upsert_history(connection, normalized)
            cutoff, latest_game = _history_cutoff(connection)
        planning = planning_week_context(season, at=now, operational=context)
        forecast_weeks = sorted({active_week, int(planning.get("week", active_week))})
        with _pipeline_connection() as connection:
            forecasts, unchanged, frozen = _refresh_forecasts(
                connection, run_id=run_id, season=season, weeks=forecast_weeks,
                schedule_loader=schedule_loader, generated_at=now,
            )
            connection.execute("""UPDATE nfl_data_pipeline_runs SET completed_at=?,status='SUCCEEDED',
                weeks_checked=?,games_observed=?,history_rows_inserted=?,duplicate_rows=?,conflict_rows=?,
                forecasts_created=?,forecasts_unchanged=?,officials_frozen=?,latest_history_cutoff=?,details_json=?
                WHERE run_id=?""", (_iso(now), len(weeks), games, inserted, duplicates, conflicts,
                forecasts, unchanged, frozen, cutoff,
                _canonical({"weeks": weeks, "forecastWeeks": forecast_weeks}), run_id))
            connection.execute("""UPDATE nfl_data_pipeline_state SET last_success_at=?,last_status='SUCCEEDED',
                last_error_code=NULL,consecutive_failures=0,last_season=?,last_week=?,latest_game_id=?,
                latest_history_cutoff=?,last_forecast_refresh_at=?,updated_at=? WHERE singleton=1""",
                (_iso(now), season, active_week, latest_game, cutoff, _iso(now), _iso(now)))
        return {"runId": run_id, "status": "SUCCEEDED", "season": season,
                "weeksChecked": weeks, "gamesObserved": games, "historyRowsInserted": inserted,
                "duplicateRows": duplicates, "conflictRows": conflicts,
                "forecastsCreated": forecasts, "forecastsUnchanged": unchanged,
                "officialsFrozen": frozen, "latestHistoryCutoff": cutoff,
                "networkContacted": True}
    except Exception as exc:
        code = str(exc) if isinstance(exc, (HistoryConflict, RuntimeError, ValueError)) else type(exc).__name__
        code = code[:80]
        logger.exception("nfl_current_season_pipeline_failed code=%s", code)
        with _pipeline_connection() as connection:
            connection.execute("""UPDATE nfl_data_pipeline_runs SET completed_at=?,status='FAILED',
                safe_error_code=? WHERE run_id=?""", (_iso(_now(clock)), code, run_id))
            connection.execute("""UPDATE nfl_data_pipeline_state SET last_status='FAILED',last_error_code=?,
                consecutive_failures=consecutive_failures+1,updated_at=? WHERE singleton=1""",
                (code, _iso(_now(clock))))
        raise
    finally:
        _release_pipeline_lock(lock_connection)


def status() -> dict:
    initialize()
    with _pipeline_connection() as connection:
        state = connection.execute("SELECT * FROM nfl_data_pipeline_state WHERE singleton=1").fetchone()
        counts = connection.execute("""SELECT season,COUNT(DISTINCT game_id) AS games,COUNT(*) AS team_rows,
            MAX(completed_at) AS latest_cutoff FROM nfl_team_game_history GROUP BY season ORDER BY season""").fetchall()
        latest = connection.execute("""SELECT run_id,started_at,completed_at,status,safe_error_code,
            weeks_checked,games_observed,history_rows_inserted,duplicate_rows,conflict_rows,
            forecasts_created,forecasts_unchanged,officials_frozen,latest_history_cutoff
            FROM nfl_data_pipeline_runs ORDER BY started_at DESC LIMIT 10""").fetchall()
        forecast_summary = connection.execute("""SELECT COUNT(*) AS snapshots,
            COUNT(DISTINCT game_id) AS games,MAX(generated_at) AS latest_generated_at,
            MAX(history_cutoff) AS latest_history_cutoff
            FROM nfl_forecast_snapshots""").fetchone()
        official_integrity = {"count": 0, "maxId": 0, "digest": None}
        job = latest_run = None
        if _pipeline_using_postgres():
            official = connection.execute("""SELECT COUNT(*) AS count,
                COALESCE(MAX(id),0) AS max_id,
                MD5(COALESCE(STRING_AGG(TO_JSONB(t)::text,'' ORDER BY id),'')) AS digest
                FROM nfl_game_predictions t WHERE user_id=0""").fetchone()
            official_integrity = {
                "count": _row_value(official, "count", 0),
                "maxId": _row_value(official, "max_id", 0),
                "digest": _row_value(official, "digest"),
            }
            cron_state = connection.execute(
                "SELECT public.nfl_data_refresh_cron_status(?) AS value",
                (_deployment_environment(),),
            ).fetchone()["value"]
            job = cron_state.get("job") if cron_state else None
            latest_run = cron_state.get("latestRun") if cron_state else None
    return {"scheduler": "supabase_cron", "enabled": _enabled(),
            "state": dict(state) if state else None, "history": [dict(row) for row in counts],
            "runs": [dict(row) for row in latest], "cronJob": job,
            "latestCronRun": latest_run,
            "forecasts": dict(forecast_summary) if forecast_summary else None,
            "officialPredictionIntegrity": official_integrity,
            "modelVersionChanged": False, "historicalPredictionsMutable": False}


def readiness() -> dict:
    """Return sanitized deployment identity and prerequisite state without writes."""
    environment = _deployment_environment()
    result = {
        "environment": environment,
        "refreshEnabled": _enabled(),
        "installerEnabled": _install_enabled(),
        "dedicatedDatabaseUrlConfigured": bool(
            (os.getenv("NFL_DATA_REFRESH_DATABASE_URL") or "").strip()
        ),
        "schedulerExpectedName": (
            _job_name(environment) if environment in DEPLOYMENT_ENVIRONMENTS else None
        ),
        "schedulerExpectedSchedule": (
            _expected_schedule(environment)
            if environment in DEPLOYMENT_ENVIRONMENTS else None
        ),
        "schedulerTimeZone": (
            PRODUCTION_TIME_ZONE if environment == "production" else "UTC"
        ),
        "schedulerLocalTime": (
            PRODUCTION_LOCAL_TIME if environment == "production" else None
        ),
        "database": {"kind": "postgres" if _pipeline_using_postgres() else "sqlite"},
    }
    if not _pipeline_using_postgres():
        result["ready"] = False
        result["blockers"] = ["PERSISTENT_POSTGRES_REQUIRED"]
        return result

    parsed = urlparse(_pipeline_database_url())
    result["database"]["host"] = parsed.hostname
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    if supabase_url:
        supabase_host = urlparse(supabase_url).hostname or ""
        if supabase_host.endswith(".supabase.co"):
            result["database"]["supabaseProjectRef"] = supabase_host.split(".", 1)[0]
    with _pipeline_connection() as connection:
        identity = connection.execute("""SELECT current_user AS current_role,
            current_database() AS database_name,
            current_setting('server_version') AS server_version,
            to_regclass('public.nfl_team_game_history') IS NOT NULL AS history_installed,
            to_regprocedure('public.nfl_data_refresh_cron_status(text)') IS NOT NULL
                AS environment_boundary_installed""").fetchone()
        cron_job = None
        if _row_value(identity, "environment_boundary_installed", False):
            cron_state = connection.execute(
                "SELECT public.nfl_data_refresh_cron_status(?) AS value",
                (environment,),
            ).fetchone()["value"]
            cron_job = cron_state.get("job") if cron_state else None
    current_role = _row_value(identity, "current_role")
    result["database"].update({
        "name": _row_value(identity, "database_name"),
        "serverVersion": _row_value(identity, "server_version"),
        "currentRole": current_role,
        "leastPrivilegeRole": current_role in {
            "smartbets_staging_app", "smartbets_production_app"
        },
    })
    result["migrationInstalled"] = bool(_row_value(identity, "history_installed", False))
    result["environmentBoundaryInstalled"] = bool(
        _row_value(identity, "environment_boundary_installed", False)
    )
    result["cronJob"] = cron_job
    blockers = []
    if environment not in DEPLOYMENT_ENVIRONMENTS:
        blockers.append("DEPLOYMENT_ENVIRONMENT_REQUIRED")
    if not result["migrationInstalled"] or not result["environmentBoundaryInstalled"]:
        blockers.append("NFL_PIPELINE_MIGRATIONS_REQUIRED")
    if not result["database"]["leastPrivilegeRole"]:
        blockers.append("DEDICATED_DATABASE_ROLE_REQUIRED")
    if environment == "production" and not result["dedicatedDatabaseUrlConfigured"]:
        blockers.append("DEDICATED_DATABASE_URL_REQUIRED")
    if environment == "production":
        if _enabled():
            if not cron_job:
                blockers.append("PRODUCTION_CRON_REQUIRED_WHEN_ENABLED")
            elif cron_job.get("jobname") != PRODUCTION_JOB_NAME:
                blockers.append("PRODUCTION_CRON_NAME_INVALID")
            elif cron_job.get("schedule") != PRODUCTION_CRON_SCHEDULE:
                blockers.append("PRODUCTION_CRON_SCHEDULE_INVALID")
            elif not cron_job.get("active"):
                blockers.append("PRODUCTION_CRON_INACTIVE")
            if _install_enabled():
                blockers.append("PRODUCTION_INSTALLER_MUST_BE_DISABLED_AFTER_INSTALL")
        else:
            if cron_job:
                blockers.append("PRODUCTION_CRON_PRESENT_WHILE_REFRESH_DISABLED")
            if _install_enabled():
                blockers.append("PRODUCTION_INSTALLER_OPEN_BEFORE_ACTIVATION")
    result["blockers"] = blockers
    result["ready"] = not blockers
    return result


def install_supabase_cron(*, base_url: str, secret: str) -> dict:
    if not _pipeline_using_postgres():
        raise RuntimeError("Supabase Cron installation requires PostgreSQL")
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path not in {"", "/"}:
        raise ValueError("NFL_DATA_REFRESH_BASE_URL must be an HTTPS origin")
    if len(secret) < 32:
        raise ValueError("NFL_DATA_REFRESH_SECRET must contain at least 32 characters")
    environment = _deployment_environment()
    if environment not in DEPLOYMENT_ENVIRONMENTS:
        raise RuntimeError("NFL_DATA_REFRESH_ENVIRONMENT_REQUIRED")
    target = base_url.rstrip("/") + "/api/cron/nfl-data-refresh"
    with _pipeline_connection() as connection:
        result = connection.execute(
            "SELECT public.install_nfl_data_refresh_cron(?,?,?) AS value",
            (base_url.rstrip("/"), secret, environment),
        ).fetchone()["value"]
    return result or {"installed": False, "target": target}
