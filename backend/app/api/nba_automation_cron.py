"""Authenticated bounded NBA synchronization and settlement endpoint."""

from __future__ import annotations

import os
import logging

from fastapi import APIRouter, HTTPException, Request

from backend.app.api.social_cron import authorized
from backend.app.services.nba.availability import personnel_refresh_guard, refresh_personnel
from backend.app.services.nba.automation import run_cycle
from backend.app.services.nba import data_refresh
from backend.app.services.nba import production_migration
from backend.app.services.nba.lifecycle import foundation_status, operational_settings
from backend.app.services.nba.season import active_nba_season

router = APIRouter()
logger = logging.getLogger(__name__)
ALLOWED_REFRESH_TRIGGERS = {"supabase_cron", "production_activation"}


def _authorized(request: Request) -> bool:
    return (
        authorized(request, "NBA_AUTOMATION_SECRET")
        or authorized(request, "NBA_RECONCILIATION_SECRET")
        or authorized(request, "CRON_SECRET")
    )


def _personnel_authorized(request: Request) -> bool:
    return authorized(request, "NBA_PERSONNEL_REFRESH_SECRET")


def _refresh_authorized(request: Request) -> bool:
    return authorized(request, "NBA_DATA_REFRESH_SECRET")


def _refresh_trigger(request: Request) -> str:
    requested = request.headers.get("x-smartbets-trigger", "supabase_cron")
    return requested if requested in ALLOWED_REFRESH_TRIGGERS else "supabase_cron"


@router.post("/api/cron/nba-data-refresh")
def nba_data_refresh_tick(request: Request):
    if not _refresh_authorized(request):
        raise HTTPException(401, "NBA data refresh authentication required")
    try:
        return data_refresh.run(trigger_source=_refresh_trigger(request))
    except Exception:
        raise HTTPException(
            503, "NBA data refresh failed; inspect protected operational status"
        ) from None


@router.get("/api/cron/nba-data-refresh/status")
def nba_data_refresh_status(request: Request):
    if not _refresh_authorized(request):
        raise HTTPException(401, "NBA data refresh authentication required")
    try:
        return data_refresh.status()
    except Exception:
        raise HTTPException(503, "NBA data refresh status is unavailable") from None


@router.get("/api/cron/nba-data-refresh/readiness")
def nba_data_refresh_readiness(request: Request):
    if not _refresh_authorized(request):
        raise HTTPException(401, "NBA data refresh authentication required")
    try:
        return data_refresh.readiness()
    except Exception:
        raise HTTPException(503, "NBA data refresh readiness is unavailable") from None


@router.post("/api/cron/nba-data-refresh/install")
def install_nba_data_refresh(request: Request):
    if not _refresh_authorized(request):
        raise HTTPException(401, "NBA data refresh authentication required")
    if os.getenv("NBA_DATA_REFRESH_INSTALL_ENABLED", "false").strip().lower() != "true":
        raise HTTPException(409, "NBA data refresh installation is disabled")
    base_url = os.getenv("NBA_DATA_REFRESH_BASE_URL") or (
        f"https://{os.getenv('VERCEL_URL')}" if os.getenv("VERCEL_URL") else ""
    )
    try:
        return data_refresh.install_supabase_cron(
            base_url=base_url,
            secret=os.getenv("NBA_DATA_REFRESH_SECRET", ""),
        )
    except Exception:
        raise HTTPException(503, "NBA data refresh Cron installation failed") from None


@router.get("/api/cron/nba-data-refresh/migration-readiness")
def nba_data_refresh_migration_readiness(request: Request):
    if not _refresh_authorized(request):
        raise HTTPException(401, "NBA data refresh authentication required")
    if os.getenv("NBA_DATA_REFRESH_INSTALL_ENABLED", "false").strip().lower() != "true":
        raise HTTPException(409, "NBA data refresh installation is disabled")
    try:
        return production_migration.readiness()
    except Exception:
        raise HTTPException(503, "NBA migration readiness is unavailable") from None


@router.post("/api/cron/nba-data-refresh/migrate")
def migrate_nba_data_refresh(request: Request):
    if not _refresh_authorized(request):
        raise HTTPException(401, "NBA data refresh authentication required")
    if os.getenv("NBA_DATA_REFRESH_INSTALL_ENABLED", "false").strip().lower() != "true":
        raise HTTPException(409, "NBA data refresh installation is disabled")
    try:
        return production_migration.apply()
    except Exception:
        logger.exception("nba_data_refresh_migration_failed")
        raise HTTPException(503, "NBA data refresh migration failed") from None


@router.get("/api/cron/nba-active-season")
@router.post("/api/cron/nba-active-season")
def nba_active_season_tick(request: Request):
    if not _authorized(request):
        raise HTTPException(401, "NBA automation authentication required")
    settings = operational_settings()
    season = active_nba_season(settings.get("seasonOverride")).code
    if not settings["enabled"]:
        return {"enabled": False, "season": season}
    try:
        return {"enabled": True, **run_cycle(season=season, trigger_source="vercel_cron")}
    except Exception:
        raise HTTPException(503, "NBA automation cycle failed; inspect protected operational logs") from None


@router.get("/api/cron/nba-active-season/status")
def nba_active_season_status(request: Request):
    if not _authorized(request):
        raise HTTPException(401, "NBA automation authentication required")
    try:
        settings = operational_settings()
        season = active_nba_season(settings.get("seasonOverride")).code
        return {"enabled": settings["enabled"], **foundation_status(season)}
    except Exception:
        raise HTTPException(503, "NBA automation status is unavailable") from None


@router.post("/api/cron/nba-personnel")
def nba_personnel_tick(request: Request):
    """Refresh immutable roster/availability evidence without running predictions."""
    if not _personnel_authorized(request):
        raise HTTPException(401, "NBA personnel refresh authentication required")
    settings = operational_settings()
    season = active_nba_season(settings.get("seasonOverride")).code
    if str(os.getenv("NBA_PERSONNEL_REFRESH_ENABLED") or "").strip().lower() not in {
        "1", "true", "yes", "on",
    }:
        return {"status": "DISABLED", "season": season}
    try:
        with personnel_refresh_guard() as acquired:
            if not acquired:
                return {"status": "SKIPPED_OVERLAP", "season": season}
            return refresh_personnel(season=season)
    except Exception:
        raise HTTPException(
            503, "NBA personnel refresh failed; inspect protected operational logs"
        ) from None
