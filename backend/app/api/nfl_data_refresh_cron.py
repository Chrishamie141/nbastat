"""Authenticated cloud boundary for current-season NFL history refresh."""
import os
import logging

from fastapi import APIRouter, HTTPException, Request

from backend.app.api.social_cron import authorized
from backend.app.services import nfl_current_season_pipeline

router = APIRouter()
logger = logging.getLogger(__name__)
ALLOWED_TRIGGER_SOURCES = {"supabase_cron", "production_activation"}


def _trigger_source(request: Request) -> str:
    requested = request.headers.get("x-smartbets-trigger", "supabase_cron")
    return requested if requested in ALLOWED_TRIGGER_SOURCES else "supabase_cron"


@router.post("/api/cron/nfl-data-refresh")
def nfl_data_refresh(request: Request):
    if not authorized(request, "NFL_DATA_REFRESH_SECRET"):
        raise HTTPException(401, "NFL data refresh authentication required")
    try:
        return nfl_current_season_pipeline.run(
            trigger_source=_trigger_source(request)
        )
    except Exception:
        logger.exception("nfl_data_refresh_request_failed")
        raise HTTPException(
            503, "NFL data refresh failed; inspect protected pipeline status"
        ) from None


@router.get("/api/cron/nfl-data-refresh/status")
def nfl_data_refresh_status(request: Request):
    if not authorized(request, "NFL_DATA_REFRESH_SECRET"):
        raise HTTPException(401, "NFL data refresh authentication required")
    try:
        return nfl_current_season_pipeline.status()
    except Exception:
        logger.exception("nfl_data_refresh_status_failed")
        raise HTTPException(503, "NFL data refresh status is unavailable") from None


@router.get("/api/cron/nfl-data-refresh/readiness")
def nfl_data_refresh_readiness(request: Request):
    if not authorized(request, "NFL_DATA_REFRESH_SECRET"):
        raise HTTPException(401, "NFL data refresh authentication required")
    try:
        return nfl_current_season_pipeline.readiness()
    except Exception:
        logger.exception("nfl_data_refresh_readiness_failed")
        raise HTTPException(503, "NFL data refresh readiness check failed") from None


@router.post("/api/cron/nfl-data-refresh/install")
def install_nfl_data_refresh(request: Request):
    if not authorized(request, "NFL_DATA_REFRESH_SECRET"):
        raise HTTPException(401, "NFL data refresh authentication required")
    if os.getenv("NFL_DATA_REFRESH_INSTALL_ENABLED", "false").strip().lower() != "true":
        raise HTTPException(409, "NFL data refresh Cron installation is disabled")
    base_url = os.getenv("NFL_DATA_REFRESH_BASE_URL") or (
        f"https://{os.getenv('VERCEL_URL')}" if os.getenv("VERCEL_URL") else ""
    )
    try:
        return nfl_current_season_pipeline.install_supabase_cron(
            base_url=base_url, secret=os.getenv("NFL_DATA_REFRESH_SECRET", "")
        )
    except Exception:
        logger.exception("nfl_data_refresh_install_failed")
        raise HTTPException(503, "NFL data refresh Cron installation failed") from None
