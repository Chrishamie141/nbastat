"""Authenticated REST boundary for the Supabase-scheduled NFL automation."""
import os
import logging

from fastapi import APIRouter, HTTPException, Request

from backend.app.api.social_cron import authorized
from backend.app.services import nfl_server_automation
from backend.app.services.nfl_product_service import current_week_context, nfl_season_year
from backend.app.services.nfl_production_service import run_lifecycle

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/api/cron/nfl-active-season")
def nfl_active_season_tick(request: Request):
    if not authorized(request, "NFL_AUTOMATION_SECRET"):
        raise HTTPException(401, "NFL automation authentication required")
    try:
        capture = nfl_server_automation.tick()
        if not capture.get("experiment"):
            return capture
        try:
            context = current_week_context(nfl_season_year())
            lifecycle = run_lifecycle(
                season=int(context["season"]), season_type=str(context["seasonType"]),
                week=int(context["week"]), max_benchmark_games=1,
            )
        except Exception as lifecycle_error:
            # Market capture and settlement are independently retryable. A
            # lifecycle dependency failure must be visible without making a
            # successful capture invocation look as though it never ran.
            logger.exception("nfl_production_lifecycle_retry_required", extra={"error_code": type(lifecycle_error).__name__})
            lifecycle = None
        return {**capture, **({"productionLifecycle": lifecycle} if lifecycle is not None else {})}
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
