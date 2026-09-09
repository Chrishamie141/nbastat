"""Media generation orchestration, caps, immutable versions and metadata."""
from __future__ import annotations

from datetime import datetime, timezone
import os
import logging
import uuid
from typing import Any

from .assets import storage_factory
from .graphic_renderer import HEIGHT, WIDTH, render_graphic
from .image_generator import OpenAIImageGenerator
from .prompts import creative_prompt
from .video_renderer import VIDEO_HEIGHT, VIDEO_WIDTH, render_video
from ..storage import decoded

logger = logging.getLogger(__name__)


def _configured_cost(name: str) -> float | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    value = float(raw)
    return round(value, 6) if value >= 0 else None


def _today_count(connection, media_type: str, at: datetime) -> int:
    row = connection.execute(
        "SELECT COUNT(*) AS count FROM social_media_assets WHERE media_type=? AND status='READY' AND generated_at LIKE ?",
        (media_type, at.date().isoformat() + "%"),
    ).fetchone()
    return int(row["count"])


def generate_media(connection, opportunity: dict[str, Any], post: dict[str, Any], context: dict[str, Any],
                   settings: dict[str, Any], sha, *, requested_type: str = "image", storage=None,
                   image_provider_factory=OpenAIImageGenerator, clock=None) -> dict[str, Any] | None:
    at = (clock or (lambda: datetime.now(timezone.utc)))().astimezone(timezone.utc)
    requested_type = requested_type.lower()
    if requested_type == "video" and not settings["video_enabled"]:
        return None
    if requested_type == "image" and not settings["ai_images_enabled"] and os.getenv("SOCIAL_DETERMINISTIC_GRAPHICS_ENABLED", "true").lower() != "true":
        return None
    cap_key = "max_videos_per_day" if requested_type == "video" else "max_images_per_day"
    if _today_count(connection, requested_type, at) >= int(settings[cap_key]):
        raise RuntimeError(f"SOCIAL_{requested_type.upper()}_DAILY_CAP")
    prior = connection.execute(
        "SELECT MAX(version) AS version FROM social_media_assets WHERE opportunity_id=? AND media_type=?",
        (opportunity["opportunity_id"], requested_type),
    ).fetchone()
    version = int(prior["version"] or 0) + 1
    asset_id = uuid.uuid4().hex
    template = opportunity["content_type"]
    prompt = creative_prompt(context, template)
    prompt_hash = sha(prompt)
    source_hash = opportunity["source_hash"]
    generated_at = at.isoformat()
    connection.execute("""INSERT INTO social_media_assets(
        media_asset_id,opportunity_id,post_id,game_id,media_type,template,provider,provider_model,
        generation_quality,prompt_hash,source_hash,status,version,generated_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,'GENERATING',?,?,?)""", (
        asset_id, opportunity["opportunity_id"], post["post_id"], opportunity.get("game_id"), requested_type,
        template, "deterministic", None, settings["image_quality"], prompt_hash, source_hash,
        version, generated_at, generated_at,
    ))
    try:
        background = None; provider = "deterministic"; provider_model = None; cost = None
        if settings["ai_images_enabled"]:
            try:
                image_provider = image_provider_factory()
                background, provider_model = image_provider.generate(prompt, quality=settings["image_quality"])
                provider = "openai"
                cost = _configured_cost("SOCIAL_IMAGE_COST_ESTIMATE")
            except Exception:
                if os.getenv("SOCIAL_MEDIA_FAILURE_POLICY", "deterministic").lower() != "deterministic":
                    raise
        image = render_graphic(context, template, background)
        if requested_type == "video":
            payload = render_video(image); extension = "mp4"; content_type = "video/mp4"
            duration = 15.0; width, height = VIDEO_WIDTH, VIDEO_HEIGHT
            cost = (cost or 0) + (_configured_cost("SOCIAL_VIDEO_COST_ESTIMATE") or 0) if (
                cost is not None or _configured_cost("SOCIAL_VIDEO_COST_ESTIMATE") is not None) else None
        else:
            payload = image; extension = "png"; content_type = "image/png"
            duration = None; width, height = WIDTH, HEIGHT
        key = f"{at:%Y/%m/%d}/{asset_id}-v{version}.{extension}"
        location = (storage or storage_factory()).put(key, payload, content_type)
        alt_text = f"SmartBetSports {template.replace('_', ' ').title()} graphic"
        if context.get("away_team") and context.get("home_team"):
            alt_text += f" for {context['away_team']} at {context['home_team']}"
        connection.execute("""UPDATE social_media_assets SET provider=?,provider_model=?,storage_key=?,storage_url=?,
            status='READY',width=?,height=?,duration_seconds=?,alt_text=?,cost_estimate=?,updated_at=? WHERE media_asset_id=?""",
            (provider, provider_model, key, location, width, height, duration, alt_text, cost, at.isoformat(), asset_id))
        logger.info("social_media_ready", extra={"mediaAssetId": asset_id, "mediaType": requested_type,
                                                 "provider": provider, "template": template})
        return {
            "media_asset_id": asset_id, "opportunity_id": opportunity["opportunity_id"],
            "post_id": post["post_id"], "game_id": opportunity.get("game_id"), "media_type": requested_type,
            "template": template, "provider": provider, "provider_model": provider_model,
            "generation_quality": settings["image_quality"], "prompt_hash": prompt_hash,
            "source_hash": source_hash, "storage_key": key, "storage_url": location,
            "status": "READY", "width": width, "height": height, "duration_seconds": duration,
            "alt_text": alt_text, "version": version, "cost_estimate": cost, "generated_at": generated_at,
        }
    except Exception as exc:
        code = str(exc) if str(exc).startswith(("OPENAI_", "FFMPEG_", "VIDEO_", "SOCIAL_")) else type(exc).__name__.upper()
        connection.execute("UPDATE social_media_assets SET status='FAILED',error_code=?,updated_at=? WHERE media_asset_id=?",
                           (code[:80], at.isoformat(), asset_id))
        logger.warning("social_media_failed", extra={"mediaAssetId": asset_id, "mediaType": requested_type,
                                                      "errorCode": code[:80]})
        raise


def regenerate_media(connection, post_id: str, settings: dict[str, Any], sha, *, media_type: str | None = None,
                     storage=None, image_provider_factory=OpenAIImageGenerator, clock=None) -> dict[str, Any]:
    row = connection.execute("""SELECT d.source_context_json,o.* FROM social_post_details d
        JOIN social_opportunities o ON o.opportunity_id=d.opportunity_id WHERE d.post_id=?""", (post_id,)).fetchone()
    if not row:
        raise ValueError("Social post does not support media regeneration")
    opportunity = dict(row); context = decoded(row["source_context_json"], {})
    post = {"post_id": post_id}
    result = generate_media(connection, opportunity, post, context, settings, sha,
                            requested_type=media_type or "image", storage=storage,
                            image_provider_factory=image_provider_factory, clock=clock)
    if not result:
        raise ValueError("Requested media type is disabled")
    existing = connection.execute("SELECT media_asset_ids_json FROM social_post_details WHERE post_id=?", (post_id,)).fetchone()
    ids = decoded(existing["media_asset_ids_json"], []) + [result["media_asset_id"]]
    connection.execute("UPDATE social_post_details SET media_asset_ids_json=?,media_type=?,alt_text=?,updated_at=? WHERE post_id=?",
                       (__import__("json").dumps(ids), result["media_type"], result["alt_text"], result["generated_at"], post_id))
    return result
