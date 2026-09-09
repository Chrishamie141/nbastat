"""Authenticated REST boundary for the Supabase-scheduled NFL automation."""
import os

from fastapi import APIRouter, HTTPException, Request

from backend.app.api.social_cron import authorized
from backend.app.services import nfl_server_automation

router = APIRouter()


@router.post("/api/cron/nfl-active-season")
def nfl_active_season_tick(request: Request):
    if not authorized(request, "NFL_AUTOMATION_SECRET"):
        raise HTTPException(401, "NFL automation authentication required")
    try:
        return nfl_server_automation.tick()
    except Exception:
        # Provider bodies, database URLs, and credentials must never cross this boundary.
        raise HTTPException(503, "NFL automation tick failed; inspect protected operational status") from None


@router.get("/api/cron/nfl-active-season/status")
def nfl_active_season_status(request: Request):
    if not authorized(request, "NFL_AUTOMATION_SECRET"):
        raise HTTPException(401, "NFL automation authentication required")
    try:
        return nfl_server_automation.status()
    except Exception:
        raise HTTPException(503, "NFL automation status is unavailable") from None


@router.post("/api/cron/nfl-active-season/install")
def install_nfl_active_season(request: Request):
    if not authorized(request, "NFL_AUTOMATION_SECRET"):
        raise HTTPException(401, "NFL automation authentication required")
    secret = os.getenv("NFL_AUTOMATION_SECRET", "")
    base_url = os.getenv("NFL_AUTOMATION_BASE_URL", "")
    try:
        return nfl_server_automation.install_supabase_cron(base_url=base_url, secret=secret)
    except Exception:
        raise HTTPException(503, "Supabase Cron installation failed") from None
