"""Authenticated owner API for the SmartBetSports social control room."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from backend.app.services.entitlement_service import require_internal_access
from backend.app.services import social_marketing
from backend.app.services import social_operations_service as operations
from backend.app.services.operator_action_service import start, finish
from backend.app.services.social import engagement


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/internal/operations/social", tags=["internal-social"],
                   dependencies=[Depends(require_internal_access)])


def _actor(user) -> str:
    try:
        return str(user.get("email") or user["id"])
    except (AttributeError, KeyError, TypeError):
        return "internal-owner"


def _write(action: str, target: str, user, callback):
    action_id = start(action, _actor(user), target)
    try:
        result = callback()
        finish(action_id, result="SUCCEEDED", status_change=str(result.get("status") or "UPDATED"))
        return {"actionId": action_id, **result}
    except (ValueError, RuntimeError) as exc:
        finish(action_id, result="FAILED", error=type(exc).__name__)
        logger.warning("social_owner_action_blocked", extra={"action": action, "target": target, "errorCode": type(exc).__name__})
        raise HTTPException(409, str(exc)) from None
    except Exception as exc:
        finish(action_id, result="FAILED", error=type(exc).__name__)
        logger.exception("social_owner_action_failed", extra={"action": action, "target": target})
        raise HTTPException(503, "Social operation failed safely; no credentials or provider payloads were exposed.") from None


@router.get("")
def social_summary():
    return operations.summary()


@router.get("/queue")
def social_queue(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    return operations.queue(limit, offset)


@router.get("/opportunities")
def social_opportunities(status: str | None = Query(None), limit: int = Query(50, ge=1, le=100),
                         offset: int = Query(0, ge=0)):
    allowed = {"DRAFT", "QUEUED", "READY", "REVIEW", "MEDIA_GENERATING", "PUBLISHING", "PUBLISHED", "FAILED", "UNKNOWN", "SKIPPED", "CANCELLED"}
    if status and status.upper() not in allowed:
        raise HTTPException(422, "Unsupported opportunity status")
    return operations.opportunities(status=status.upper() if status else None, limit=limit, offset=offset)


@router.get("/skipped")
def social_skipped(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    return operations.skipped(limit, offset)


@router.get("/posts/{post_id}")
def social_post(post_id: str):
    try:
        return operations.post_detail(post_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None


@router.get("/media")
def social_media(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    return operations.media_library(limit, offset)


@router.get("/media/{media_asset_id}/content")
def social_media_content(media_asset_id: str):
    try:
        payload, media_type = operations.media_content(media_asset_id)
        return Response(payload, media_type=media_type, headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None


@router.get("/analytics")
def social_analytics():
    return operations.analytics_report()


@router.get("/engagement")
def social_engagement_queue(limit: int = Query(50, ge=1, le=100)):
    return operations.engagement_queue(limit)


@router.get("/settings")
def social_settings():
    return operations.get_settings()


@router.patch("/settings")
def social_update_settings(payload: dict, user=Depends(require_internal_access)):
    return _write("SOCIAL_UPDATE_SETTINGS", "social-settings", user,
                  lambda: {"status": "UPDATED", "settings": operations.save_settings(payload, _actor(user))})


@router.post("/pause")
def social_pause(user=Depends(require_internal_access)):
    return _write("SOCIAL_PAUSE_ALL", "social-engine", user,
                  lambda: {"status": "PAUSED", "settings": operations.pause(True, _actor(user))})


@router.post("/resume")
def social_resume(user=Depends(require_internal_access)):
    return _write("SOCIAL_RESUME", "social-engine", user,
                  lambda: {"status": "DISCOVERY", "settings": operations.pause(False, _actor(user))})


@router.post("/discover")
def social_discover(user=Depends(require_internal_access)):
    return _write("SOCIAL_DISCOVER_OPPORTUNITIES", "social-engine", user, social_marketing.discover_opportunities)


@router.post("/process")
def social_process(user=Depends(require_internal_access)):
    return _write("SOCIAL_PROCESS_QUEUE", "social-engine", user, social_marketing.process_queue)


@router.patch("/posts/{post_id}/caption")
def social_edit_caption(post_id: str, payload: dict, user=Depends(require_internal_access)):
    return _write("SOCIAL_EDIT_CAPTION", post_id, user,
                  lambda: operations.update_caption(post_id, str(payload.get("caption") or "")))


@router.post("/posts/{post_id}/caption/regenerate")
def social_regenerate_caption(post_id: str, user=Depends(require_internal_access)):
    return _write("SOCIAL_REGENERATE_CAPTION", post_id, user, lambda: operations.regenerate_caption(post_id))


@router.post("/posts/{post_id}/template")
def social_change_template(post_id: str, payload: dict, user=Depends(require_internal_access)):
    return _write("SOCIAL_CHANGE_TEMPLATE", post_id, user,
                  lambda: operations.change_template(post_id, str(payload.get("template") or "")))


@router.post("/posts/{post_id}/media/regenerate")
def social_regenerate_media(post_id: str, payload: dict, user=Depends(require_internal_access)):
    media_type = str(payload.get("mediaType") or "image").lower()
    if media_type not in {"image", "video"}:
        raise HTTPException(422, "Unsupported media type")
    return _write("SOCIAL_REGENERATE_MEDIA", post_id, user, lambda: operations.regenerate_post_media(post_id, media_type))


@router.post("/posts/{post_id}/publish")
def social_publish(post_id: str, user=Depends(require_internal_access)):
    return _write("SOCIAL_PUBLISH_NOW", post_id, user, lambda: operations.publish_now(post_id))


@router.post("/opportunities/{opportunity_id}/reschedule")
def social_reschedule(opportunity_id: str, payload: dict, user=Depends(require_internal_access)):
    return _write("SOCIAL_RESCHEDULE", opportunity_id, user,
                  lambda: operations.reschedule(opportunity_id, str(payload.get("scheduledAt") or "")))


@router.post("/opportunities/{opportunity_id}/cancel")
def social_cancel(opportunity_id: str, user=Depends(require_internal_access)):
    return _write("SOCIAL_CANCEL", opportunity_id, user, lambda: operations.cancel(opportunity_id))


@router.post("/metrics/refresh")
def social_refresh_metrics(user=Depends(require_internal_access)):
    return _write("SOCIAL_REFRESH_METRICS", "x-metrics", user, operations.refresh_metrics)


@router.post("/engagement/{engagement_id}/{decision}")
def social_engagement_decision(engagement_id: str, decision: str, user=Depends(require_internal_access)):
    if decision not in {"approve", "reject"}:
        raise HTTPException(422, "Unsupported engagement decision")
    def callback():
        social_marketing.initialize()
        with social_marketing.connection() as connection:
            return engagement.decide(connection, engagement_id, decision == "approve")
    return _write(f"SOCIAL_ENGAGEMENT_{decision.upper()}", engagement_id, user, callback)
