"""Authenticated bounded NBA synchronization and settlement endpoint."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request

from backend.app.api.social_cron import authorized
from backend.app.services.nba.availability import personnel_refresh_guard, refresh_personnel
from backend.app.services.nba.automation import run_cycle
from backend.app.services.nba.lifecycle import foundation_status, operational_settings
from backend.app.services.nba.season import active_nba_season

router = APIRouter()


def _authorized(request: Request) -> bool:
    return (
        authorized(request, "NBA_AUTOMATION_SECRET")
        or authorized(request, "NBA_RECONCILIATION_SECRET")
        or authorized(request, "CRON_SECRET")
    )


def _personnel_authorized(request: Request) -> bool:
    return authorized(request, "NBA_PERSONNEL_REFRESH_SECRET")


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
