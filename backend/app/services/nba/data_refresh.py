"""Cloud-only NBA current-season ingestion and forecast refresh boundary."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from backend.app.database import (
    column_exists,
    database_url,
    database_url_scope,
    get_db_connection,
    is_postgres_url,
    table_exists,
)
from .automation import _result_reconciliation_guard, run_cycle
from .lifecycle import foundation_status
from .season import active_nba_season

PRODUCTION_JOB_NAME = "smartbets-nba-data-refresh-daily-production"
PRODUCTION_CRON_SCHEDULE = "0 7,8 * * *"
PRODUCTION_TIME_ZONE = "America/New_York"
PRODUCTION_LOCAL_TIME = "03:00"
DEPLOYMENT_ENVIRONMENTS = {"staging", "production"}


def _enabled() -> bool:
    return os.getenv("NBA_DATA_REFRESH_ENABLED", "false").strip().lower() == "true"


def _install_enabled() -> bool:
    return os.getenv("NBA_DATA_REFRESH_INSTALL_ENABLED", "false").strip().lower() == "true"


def _pipeline_database_url() -> str:
    # Reuse the already-deployed least-privilege production automation role
    # until a separately named NBA credential is provisioned.
    return (
        (os.getenv("NBA_DATA_REFRESH_DATABASE_URL") or "").strip()
        or (os.getenv("NFL_DATA_REFRESH_DATABASE_URL") or "").strip()
        or database_url()
    )


@contextmanager
def pipeline_database_scope():
    with database_url_scope(_pipeline_database_url()):
        yield


def _deployment_environment() -> str:
    explicit = os.getenv("NBA_DATA_REFRESH_ENVIRONMENT", "").strip().lower()
    if explicit:
        if explicit not in DEPLOYMENT_ENVIRONMENTS:
            raise RuntimeError("NBA_DATA_REFRESH_ENVIRONMENT_INVALID")
        return explicit
    return "production" if os.getenv("VERCEL_ENV", "").strip().lower() == "production" else "staging"


def _job_name(environment: str | None = None) -> str:
    selected = environment or _deployment_environment()
    if selected == "production":
        return PRODUCTION_JOB_NAME
    return "smartbets-nba-data-refresh-30m-staging"


def _same_eastern_day(value: str, clock: datetime) -> bool:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    eastern = ZoneInfo(PRODUCTION_TIME_ZONE)
    return parsed.astimezone(eastern).date() == clock.astimezone(eastern).date()


def run(*, trigger_source: str = "supabase_cron", clock: datetime | None = None) -> dict:
    """Run one catch-up cycle using only the scoped cloud database role."""
    if not _enabled():
        return {"status": "DISABLED", "networkContacted": False}
    now = (clock or datetime.now(timezone.utc)).astimezone(timezone.utc)
    with pipeline_database_scope():
        if trigger_source == "supabase_cron":
            with get_db_connection() as connection:
                recent = connection.execute(
                    """SELECT run_id,started_at,status FROM nba_automation_runs
                       WHERE trigger_source='supabase_cron'
                         AND status IN ('RUNNING','SUCCEEDED')
                       ORDER BY started_at DESC LIMIT 2"""
                ).fetchall()
            for row in recent:
                if _same_eastern_day(row["started_at"], now):
                    return {
                        "status": "ALREADY_COMPLETED",
                        "runId": row["run_id"],
                        "networkContacted": False,
                    }
        with _result_reconciliation_guard() as acquired:
            if not acquired:
                return {"status": "ALREADY_RUNNING", "networkContacted": False}
            season = active_nba_season().code
            return {
                "status": "SUCCEEDED",
                "networkContacted": True,
                **run_cycle(season=season, trigger_source=trigger_source),
            }


def _cron_state(connection, environment: str) -> dict:
    row = connection.execute(
        "SELECT public.nba_data_refresh_cron_status(?) AS value", (environment,)
    ).fetchone()
    return (row["value"] if row else None) or {}


def status() -> dict:
    """Return protected operational metrics without credentials or raw payloads."""
    environment = _deployment_environment()
    with pipeline_database_scope():
        season = active_nba_season().code
        foundation = foundation_status(season)
        with get_db_connection() as connection:
            regular = connection.execute(
                """SELECT COUNT(*) AS games,MAX(kickoff_time) AS latest_kickoff,
                          MAX(retrieved_at) AS latest_retrieved
                   FROM nba_games
                   WHERE season_code=? AND season_phase='regular_season'
                     AND status='FINAL'""",
                (season,),
            ).fetchone()
            latest_game = connection.execute(
                """SELECT game_id,kickoff_time,home_team,away_team,home_score,away_score,
                          retrieved_at
                   FROM nba_games
                   WHERE season_code=? AND season_phase='regular_season' AND status='FINAL'
                   ORDER BY kickoff_time DESC,game_id DESC LIMIT 1""",
                (season,),
            ).fetchone()
            forecast_summary = connection.execute(
                """WITH ranked AS (
                     SELECT p.*,
                            ROW_NUMBER() OVER (
                              PARTITION BY p.game_id
                              ORDER BY p.generated_at DESC,p.snapshot_id DESC
                            ) AS rank
                     FROM nba_prediction_snapshots p
                     JOIN nba_games g ON g.game_id=p.game_id
                     WHERE p.season_code=? AND p.model_version='nba-game-elo-baseline-v1'
                       AND p.snapshot_kind='LATEST_FORECAST' AND g.status='SCHEDULED'
                       AND g.kickoff_time>?
                   )
                   SELECT COUNT(*) AS games,
                          SUM(CASE WHEN selection=g.home_team THEN 1 ELSE 0 END) AS home_selections,
                          SUM(CASE WHEN selection=g.away_team THEN 1 ELSE 0 END) AS away_selections,
                          MAX(generated_at) AS latest_generated_at,
                          MAX(feature_cutoff) AS latest_feature_cutoff
                   FROM ranked p JOIN nba_games g ON g.game_id=p.game_id
                   WHERE rank=1""",
                (season, now_iso()),
            ).fetchone()
            integrity = None
            if is_postgres_url(_pipeline_database_url()):
                integrity = connection.execute(
                    """SELECT COUNT(*) AS count,
                              MD5(COALESCE(STRING_AGG(
                                CONCAT_WS('|',snapshot_id,user_id,game_id,season_code,
                                  kickoff_time,market,selection,model_version,data_version,
                                  generated_at,feature_cutoff,snapshot_hash),'' ORDER BY snapshot_id
                              ),'')) AS digest
                       FROM nba_prediction_snapshots
                       WHERE snapshot_kind='OFFICIAL'"""
                ).fetchone()
            cron = _cron_state(connection, environment)
        return {
            **foundation,
            "scheduler": "supabase_cron",
            "cronJob": cron.get("job"),
            "latestCronRun": cron.get("latestRun"),
            "cronPolicy": cron.get("policy"),
            "regularSeasonFinals": dict(regular) if regular else None,
            "latestCompletedGame": dict(latest_game) if latest_game else None,
            "latestForecasts": dict(forecast_summary) if forecast_summary else None,
            "officialPredictionIntegrity": dict(integrity) if integrity else None,
            "modelLogicChanged": False,
        }


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def readiness() -> dict:
    environment = _deployment_environment()
    selected_url = _pipeline_database_url()
    result = {
        "environment": environment,
        "refreshEnabled": _enabled(),
        "installerEnabled": _install_enabled(),
        "dedicatedAutomationDatabaseUrlConfigured": bool(
            (os.getenv("NBA_DATA_REFRESH_DATABASE_URL") or "").strip()
            or (os.getenv("NFL_DATA_REFRESH_DATABASE_URL") or "").strip()
        ),
        "schedulerExpectedName": _job_name(environment),
        "schedulerExpectedSchedule": (
            PRODUCTION_CRON_SCHEDULE if environment == "production" else "*/30 * * * *"
        ),
        "schedulerTimeZone": PRODUCTION_TIME_ZONE if environment == "production" else "UTC",
        "schedulerLocalTime": PRODUCTION_LOCAL_TIME if environment == "production" else None,
        "database": {"kind": "postgres" if is_postgres_url(selected_url) else "sqlite"},
    }
    blockers: list[str] = []
    if not is_postgres_url(selected_url):
        blockers.append("PERSISTENT_POSTGRES_REQUIRED")
    else:
        parsed = urlparse(selected_url)
        result["database"]["host"] = parsed.hostname
        with pipeline_database_scope():
            with get_db_connection() as connection:
                identity = connection.execute(
                    """SELECT current_user AS current_role,current_database() AS database_name,
                              current_setting('server_version') AS server_version"""
                ).fetchone()
                migration_installed = (
                    table_exists(connection, "nba_prediction_snapshots", postgres=True)
                    and column_exists(connection, "nba_prediction_snapshots", "snapshot_kind")
                )
                boundary_installed = connection.execute(
                    """SELECT to_regprocedure(
                         'public.nba_data_refresh_cron_status(text)') IS NOT NULL AS installed"""
                ).fetchone()["installed"]
                cron = _cron_state(connection, environment) if boundary_installed else {}
        role = identity["current_role"]
        result["database"].update({
            "name": identity["database_name"],
            "serverVersion": identity["server_version"],
            "currentRole": role,
            "leastPrivilegeRole": role in {"smartbets_staging_app", "smartbets_production_app"},
        })
        result["migrationInstalled"] = bool(migration_installed)
        result["environmentBoundaryInstalled"] = bool(boundary_installed)
        result["cronJob"] = cron.get("job")
        if not migration_installed or not boundary_installed:
            blockers.append("NBA_PIPELINE_MIGRATION_REQUIRED")
        if not result["database"]["leastPrivilegeRole"]:
            blockers.append("DEDICATED_DATABASE_ROLE_REQUIRED")
        job = cron.get("job")
        if _enabled():
            if not job:
                blockers.append("NBA_PRODUCTION_CRON_REQUIRED")
            elif environment == "production" and (
                job.get("jobname") != PRODUCTION_JOB_NAME
                or job.get("schedule") != PRODUCTION_CRON_SCHEDULE
                or not job.get("active")
            ):
                blockers.append("NBA_PRODUCTION_CRON_INVALID")
            if _install_enabled():
                blockers.append("NBA_INSTALLER_MUST_BE_DISABLED_AFTER_INSTALL")
    result["blockers"] = blockers
    result["ready"] = not blockers
    return result


def install_supabase_cron(*, base_url: str, secret: str) -> dict:
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path not in {"", "/"}:
        raise ValueError("NBA_DATA_REFRESH_BASE_URL must be an HTTPS origin")
    if len(secret) < 32:
        raise ValueError("NBA_DATA_REFRESH_SECRET must contain at least 32 characters")
    environment = _deployment_environment()
    with pipeline_database_scope():
        with get_db_connection() as connection:
            row = connection.execute(
                "SELECT public.install_nba_data_refresh_cron(?,?,?) AS value",
                (base_url.rstrip("/"), secret, environment),
            ).fetchone()
    return (row["value"] if row else None) or {"installed": False}
