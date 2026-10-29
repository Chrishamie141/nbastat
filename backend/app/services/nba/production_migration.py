"""Temporary, protected bridge for the reviewed additive NBA migration."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from urllib.parse import urlparse

from backend.app.database import database_url, get_db_connection, is_postgres_url

ROOT = Path(__file__).resolve().parents[4]
MIGRATION_VERSION = "20261029173626"
MIGRATION_NAME = "nba_daily_eastern_refresh"
MIGRATION_PATH = ROOT / "supabase" / "migrations" / f"{MIGRATION_VERSION}_{MIGRATION_NAME}.sql"


def _integrity(connection) -> dict:
    nba = connection.execute(
        """SELECT COUNT(*) AS count,
                  MD5(COALESCE(STRING_AGG(
                    CONCAT_WS('|',snapshot_id,user_id,game_id,season_code,kickoff_time,
                      market,selection,COALESCE(line::text,''),COALESCE(player_id,''),
                      COALESCE(player_name,''),COALESCE(model_probability::text,''),
                      COALESCE(projection::text,''),COALESCE(edge::text,''),
                      COALESCE(market_odds::text,''),model_version,COALESCE(data_version,''),
                      generated_at,feature_cutoff,COALESCE(source_provider,''),payload_json,
                      snapshot_hash,created_at),'' ORDER BY snapshot_id
                  ),'')) AS digest
           FROM public.nba_prediction_snapshots"""
    ).fetchone()
    settlements = connection.execute(
        """SELECT COUNT(*) AS count,
                  MD5(COALESCE(STRING_AGG(
                    CONCAT_WS('|',prediction_snapshot_id,game_id,status,
                      COALESCE(actual_value,''),COALESCE(reason,''),final_source_hash,settled_at),
                    '' ORDER BY prediction_snapshot_id
                  ),'')) AS digest
           FROM public.nba_settlements"""
    ).fetchone()
    nfl = connection.execute(
        """SELECT COUNT(*) AS count,COALESCE(MAX(id),0) AS max_id,
                  MD5(COALESCE(STRING_AGG(TO_JSONB(t)::text,'' ORDER BY id),'')) AS digest
           FROM public.nfl_game_predictions t WHERE user_id=0"""
    ).fetchone()
    crons = connection.execute(
        """SELECT jobid,jobname,schedule,active FROM cron.job ORDER BY jobid"""
    ).fetchall()
    return {
        "nbaPredictions": dict(nba),
        "nbaSettlements": dict(settlements),
        "nflOfficialPredictions": dict(nfl),
        "cronJobs": [dict(row) for row in crons],
    }


def readiness() -> dict:
    if os.getenv("VERCEL_ENV", "").strip().lower() != "production":
        return {"ready": False, "blockers": ["PRODUCTION_DEPLOYMENT_REQUIRED"]}
    selected = database_url()
    if not is_postgres_url(selected):
        return {"ready": False, "blockers": ["PRODUCTION_POSTGRES_REQUIRED"]}
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    with get_db_connection() as connection:
        identity = connection.execute(
            """SELECT current_user AS current_role,current_database() AS database_name,
                      current_setting('server_version') AS server_version"""
        ).fetchone()
        tables = connection.execute(
            """SELECT to_regclass('public.nba_prediction_snapshots') IS NOT NULL AS nba,
                      to_regclass('public.nfl_game_predictions') IS NOT NULL AS nfl,
                      to_regclass('public.nba_settlements') IS NOT NULL AS settlements"""
        ).fetchone()
        baseline = _integrity(connection) if all(tables.values()) else None
    parsed = urlparse(os.getenv("SUPABASE_URL", ""))
    project_ref = (
        parsed.hostname.split(".", 1)[0]
        if parsed.hostname and parsed.hostname.endswith(".supabase.co") else None
    )
    blockers = []
    if not all(tables.values()):
        blockers.append("PROTECTED_TABLES_MISSING")
    return {
        "ready": not blockers,
        "blockers": blockers,
        "database": {
            "name": identity["database_name"],
            "currentRole": identity["current_role"],
            "serverVersion": identity["server_version"],
            "supabaseProjectRef": project_ref,
        },
        "migration": {
            "version": MIGRATION_VERSION,
            "name": MIGRATION_NAME,
            "sha256": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
        },
        "baseline": baseline,
    }


def apply() -> dict:
    before = readiness()
    if not before["ready"]:
        raise RuntimeError("NBA_PRODUCTION_MIGRATION_NOT_READY")
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    with get_db_connection() as connection:
        connection.execute_script(sql)
        ledger_recorded = False
        if connection.execute(
            "SELECT to_regclass('supabase_migrations.schema_migrations') IS NOT NULL AS present"
        ).fetchone()["present"]:
            existing = connection.execute(
                """SELECT 1 FROM supabase_migrations.schema_migrations
                   WHERE version=?""",
                (MIGRATION_VERSION,),
            ).fetchone()
            if not existing:
                connection.execute(
                    """INSERT INTO supabase_migrations.schema_migrations
                       (version,name,statements) VALUES (?,?,ARRAY[?]::text[])""",
                    (MIGRATION_VERSION, MIGRATION_NAME,
                     f"Applied reviewed migration {MIGRATION_VERSION}_{MIGRATION_NAME}"),
                )
            ledger_recorded = True
    with get_db_connection() as connection:
        after = _integrity(connection)
        installed = connection.execute(
            """SELECT EXISTS(
                 SELECT 1 FROM information_schema.columns
                 WHERE table_schema='public' AND table_name='nba_prediction_snapshots'
                   AND column_name='snapshot_kind') AS snapshot_kind,
                       to_regprocedure('public.nba_data_refresh_cron_status(text)') IS NOT NULL
                         AS cron_boundary"""
        ).fetchone()
    protected_unchanged = all(
        before["baseline"][key] == after[key]
        for key in ("nbaPredictions", "nbaSettlements", "nflOfficialPredictions")
    )
    if not protected_unchanged:
        raise RuntimeError("PROTECTED_HISTORY_INTEGRITY_CHANGED")
    return {
        "status": "APPLIED",
        "migration": before["migration"],
        "database": before["database"],
        "installed": dict(installed),
        "ledgerRecorded": ledger_recorded,
        "protectedIntegrityUnchanged": protected_unchanged,
        "before": before["baseline"],
        "after": after,
    }
