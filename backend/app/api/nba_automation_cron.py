"""Authenticated bounded NBA synchronization and settlement endpoint."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request

from backend.app.api.social_cron import authorized
from backend.app.services.nba.automation import run_cycle
from backend.app.services.nba.lifecycle import foundation_status
from backend.app.services.nba.season import active_nba_season

router = APIRouter()


@router.post("/api/cron/nba-active-season")
def nba_active_season_tick(request: Request):
    if not authorized(request, "NBA_AUTOMATION_SECRET"):
        raise HTTPException(401, "NBA automation authentication required")
    season = active_nba_season().code
    if str(os.getenv("NBA_AUTOMATION_ENABLED", "false")).lower() != "true":
        return {"enabled": False, "season": season}
    try:
        return {"enabled": True, **run_cycle(season=season)}
    except Exception:
        raise HTTPException(503, "NBA automation cycle failed; inspect protected operational logs") from None


@router.get("/api/cron/nba-active-season/status")
def nba_active_season_status(request: Request):
    if not authorized(request, "NBA_AUTOMATION_SECRET"):
        raise HTTPException(401, "NBA automation authentication required")
    try:
        season = active_nba_season().code
        return {"enabled": str(os.getenv("NBA_AUTOMATION_ENABLED", "false")).lower() == "true",
                **foundation_status(season)}
    except Exception:
        raise HTTPException(503, "NBA automation status is unavailable") from None
