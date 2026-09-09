"""Owner-facing social control plane; never returns credentials or source payloads."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any

from backend.app.services import social_marketing as social
from backend.app.services.social import analytics, scheduler, settings
from backend.app.services.social.content import compose, context_from_event
from backend.app.services.social.media.assets import storage_factory
from backend.app.services.social.media.service import regenerate_media
from backend.app.services.social.publisher import OfficialX
from backend.app.services.social.storage import decoded


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _setup() -> None:
    social.initialize()


def _public_asset(row: dict[str, Any]) -> dict[str, Any]:
    result = {key: row.get(key) for key in (
        "media_asset_id", "opportunity_id", "post_id", "game_id", "media_type", "template",
        "provider", "provider_model", "generation_quality", "prompt_hash", "source_hash", "status",
        "width", "height", "duration_seconds", "alt_text", "version", "cost_estimate", "error_code",
        "retry_count", "generated_at", "updated_at",
    )}
    return result | {
        "previewUrl": f"/api/internal/operations/social/media/{row['media_asset_id']}/content" if row.get("status") == "READY" else None,
        "published": bool(row.get("remote_media_id")),
    }


def summary(clock=_now) -> dict[str, Any]:
    _setup(); at = clock().astimezone(timezone.utc); today = at.date().isoformat()
    with social.connection() as connection:
        configured = settings.load_settings(connection)
        post_counts = {row["status"]: int(row["count"]) for row in connection.execute(
            "SELECT status,COUNT(*) AS count FROM social_posts GROUP BY status").fetchall()}
        opportunity_counts = {row["status"]: int(row["count"]) for row in connection.execute(
            "SELECT status,COUNT(*) AS count FROM social_opportunities GROUP BY status").fetchall()}
        media_counts = {row["media_type"]: int(row["count"]) for row in connection.execute(
            "SELECT media_type,COUNT(*) AS count FROM social_media_assets WHERE status='READY' AND generated_at LIKE ? GROUP BY media_type",
            (today + "%",)).fetchall()}
        posts_today = int(connection.execute("SELECT COUNT(*) AS count FROM social_posts WHERE status='PUBLISHED' AND day_key=?", (today,)).fetchone()["count"])
        generating = int(connection.execute("SELECT COUNT(*) AS count FROM social_media_assets WHERE status='GENERATING'").fetchone()["count"])
        last = connection.execute("SELECT published_at,status FROM social_posts ORDER BY COALESCE(published_at,scheduled_at) DESC LIMIT 1").fetchone()
        next_item = connection.execute("SELECT opportunity_id,scheduled_at,content_type FROM social_opportunities WHERE status='QUEUED' ORDER BY scheduled_at LIMIT 1").fetchone()
        source = connection.execute("SELECT verified_at FROM social_sources ORDER BY verified_at DESC LIMIT 1").fetchone()
    credentials = all(bool(os.getenv(name)) for name in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"))
    engine_state = "PAUSED" if configured["paused"] else "DISCOVERY" if configured["dry_run"] or not configured["auto_publish"] else "ACTIVE"
    return {
        "engineState": engine_state, "xAccountState": "CONFIGURED" if credentials and os.getenv("X_EXPECTED_USER_ID") else "NEEDS_SETUP",
        "postsToday": posts_today, "queued": opportunity_counts.get("QUEUED", 0),
        "generating": generating, "reviewRequired": opportunity_counts.get("REVIEW", 0),
        "published": post_counts.get("PUBLISHED", 0), "failed": post_counts.get("FAILED", 0),
        "imagesGeneratedToday": media_counts.get("image", 0), "videosGeneratedToday": media_counts.get("video", 0),
        "lastPublish": last["published_at"] if last else None, "lastPublishStatus": last["status"] if last else None,
        "nextScheduledPost": dict(next_item) if next_item else None,
        "latestSourceAt": source["verified_at"] if source else None,
        "settings": configured, "publicWritesBlocked": configured["paused"] or configured["dry_run"] or not configured["auto_publish"],
    }


def opportunities(*, status: str | None = None, limit: int = 50, offset: int = 0,
                  _statuses: set[str] | None = None) -> dict[str, Any]:
    _setup(); values: list[Any] = []; where = ""
    if status:
        where = " WHERE o.status=?"; values.append(status.upper())
    elif _statuses:
        ordered = sorted(_statuses)
        where = " WHERE o.status IN (" + ",".join("?" for _ in ordered) + ")"
        values.extend(ordered)
    with social.connection() as connection:
        total = connection.execute(f"SELECT COUNT(*) AS count FROM social_opportunities o{where}", tuple(values)).fetchone()["count"]
        rows = connection.execute(f"""SELECT o.*,e.event_type,e.event_at,e.evidence_json,p.post_id,p.content,p.status AS post_status,
            a.media_asset_id,a.status AS media_status,a.media_type
            FROM social_opportunities o JOIN social_events e USING(event_id)
            LEFT JOIN social_post_details d USING(opportunity_id) LEFT JOIN social_posts p USING(post_id)
            LEFT JOIN social_media_assets a ON a.media_asset_id=(SELECT a2.media_asset_id FROM social_media_assets a2
                WHERE a2.opportunity_id=o.opportunity_id ORDER BY a2.version DESC LIMIT 1)
            {where} ORDER BY o.created_at DESC LIMIT ? OFFSET ?""", tuple(values + [limit, offset])).fetchall()
    items = []
    for raw in rows:
        row = dict(raw); evidence = decoded(row.pop("evidence_json"), {})
        row["score_reasons"] = decoded(row.pop("score_reasons_json"), [])
        row["matchup"] = " at ".join(value for value in (evidence.get("away_team"), evidence.get("home_team")) if value) or "Slate-wide"
        row["previewUrl"] = f"/api/internal/operations/social/media/{row['media_asset_id']}/content" if row.get("media_asset_id") and row.get("media_status") == "READY" else None
        items.append(row)
    return {"items": items, "total": int(total), "offset": offset, "limit": limit, "hasMore": offset + len(items) < int(total)}


def queue(limit: int = 50, offset: int = 0) -> dict[str, Any]:
    accepted = {"QUEUED", "READY", "REVIEW", "MEDIA_GENERATING", "PUBLISHING", "UNKNOWN"}
    return opportunities(limit=limit, offset=offset, _statuses=accepted)


def skipped(limit: int = 50, offset: int = 0) -> dict[str, Any]:
    return opportunities(status="SKIPPED", limit=limit, offset=offset)


def post_detail(post_id: str) -> dict[str, Any]:
    _setup()
    with social.connection() as connection:
        row = connection.execute("""SELECT p.*,d.*,e.event_type,e.event_at,e.evidence_json,o.score,o.threshold,o.decision,o.score_reasons_json
            FROM social_posts p LEFT JOIN social_post_details d USING(post_id)
            LEFT JOIN social_events e ON e.event_id=d.event_id LEFT JOIN social_opportunities o USING(opportunity_id)
            WHERE p.post_id=?""", (post_id,)).fetchone()
        if not row:
            raise ValueError("Social post not found")
        assets = [dict(item) for item in connection.execute("SELECT * FROM social_media_assets WHERE post_id=? ORDER BY version DESC", (post_id,)).fetchall()]
    result = dict(row); result["evidence"] = decoded(result.pop("evidence_json", None), {})
    result["source_context"] = decoded(result.pop("source_context_json", None), {})
    result["score_reasons"] = decoded(result.pop("score_reasons_json", None), [])
    result["media"] = [_public_asset(asset) for asset in assets]
    result.pop("signature", None)
    return result


def media_library(limit: int = 50, offset: int = 0) -> dict[str, Any]:
    _setup()
    with social.connection() as connection:
        total = int(connection.execute("SELECT COUNT(*) AS count FROM social_media_assets").fetchone()["count"])
        rows = [dict(row) for row in connection.execute("SELECT * FROM social_media_assets ORDER BY generated_at DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()]
    return {"items": [_public_asset(row) for row in rows], "total": total, "hasMore": offset + len(rows) < total}


def media_content(media_asset_id: str) -> tuple[bytes, str]:
    _setup()
    with social.connection() as connection:
        row = connection.execute("SELECT storage_key,media_type,status FROM social_media_assets WHERE media_asset_id=?", (media_asset_id,)).fetchone()
    if not row or row["status"] != "READY":
        raise ValueError("Media asset is not available")
    return storage_factory().get(row["storage_key"]), "video/mp4" if row["media_type"] == "video" else "image/png"


def get_settings() -> dict[str, Any]:
    _setup()
    with social.connection() as connection:
        return settings.load_settings(connection)


def save_settings(updates: dict[str, Any], actor: str) -> dict[str, Any]:
    _setup()
    with social.connection() as connection:
        return settings.update_settings(connection, updates, actor)


def pause(paused: bool, actor: str) -> dict[str, Any]:
    return save_settings({"paused": bool(paused)}, actor)


def update_caption(post_id: str, caption: str) -> dict[str, Any]:
    _setup()
    with social.connection() as connection:
        return scheduler.edit_caption(connection, post_id, caption, social.sha, social.sign)


def regenerate_caption(post_id: str) -> dict[str, Any]:
    detail = post_detail(post_id); context = detail.get("source_context") or {}
    caption, writer = compose(context)
    return {**update_caption(post_id, caption), "writer": writer}


TEMPLATE_GROUPS = (
    {"AI_PICK", "GAME_PREVIEW", "ENGAGEMENT_QUESTION"},
    {"MODEL_VS_MARKET", "UPSET_WATCH", "LINE_MOVEMENT"},
    {"WIN_RECEIPT", "CASHED"}, {"LOSS_RECEIPT", "MISS"},
    {"DAILY_RECAP", "WEEKLY_REPORT", "MODEL_RECAP"},
)


def change_template(post_id: str, template: str) -> dict[str, Any]:
    _setup(); template = template.upper()
    with social.connection() as connection:
        row = connection.execute("""SELECT p.post_id,p.source_id,d.post_type,d.opportunity_id,d.event_id,e.evidence_json
            FROM social_posts p JOIN social_post_details d USING(post_id) JOIN social_events e ON e.event_id=d.event_id
            WHERE p.post_id=?""", (post_id,)).fetchone()
        if not row:
            raise ValueError("Social post not found")
        group = next((group for group in TEMPLATE_GROUPS if row["post_type"] in group), None)
        if not group or template not in group:
            raise ValueError("Template is not compatible with this verified event")
        evidence = decoded(row["evidence_json"], {})
        context = context_from_event({"evidence": evidence}, template, social.cta())
        if template == "CASHED" and not context.get("stored_wager_evidence"):
            raise ValueError("CASHED requires legitimate stored wager evidence; use WIN_RECEIPT for a prediction")
        caption, writer = compose(context)
        connection.execute("UPDATE social_post_details SET post_type=?,source_context_json=?,updated_at=? WHERE post_id=?",
                           (template, social.encode(context), _now().isoformat(), post_id))
        connection.execute("UPDATE social_opportunities SET content_type=?,updated_at=? WHERE opportunity_id=?",
                           (template, _now().isoformat(), row["opportunity_id"]))
    return {**update_caption(post_id, caption), "template": template, "writer": writer}


def regenerate_post_media(post_id: str, media_type: str = "image") -> dict[str, Any]:
    _setup()
    with social.connection() as connection:
        row = connection.execute("SELECT source_id FROM social_posts WHERE post_id=?", (post_id,)).fetchone()
        if not row:
            raise ValueError("Social post not found")
        # Regeneration is a new artifact-producing action.  Re-validate the exact
        # signed source behind the post so stale or tampered evidence cannot be
        # turned into fresh-looking media.
        social.load_source(connection, row["source_id"])
        configured = settings.load_settings(connection)
        return regenerate_media(connection, post_id, configured, social.sha, media_type=media_type)


def publish_now(post_id: str, clock=_now) -> dict[str, Any]:
    _setup(); at = clock().astimezone(timezone.utc)
    with social.connection() as connection:
        row = connection.execute("SELECT opportunity_id FROM social_post_details WHERE post_id=?", (post_id,)).fetchone()
        if not row:
            raise ValueError("Only a social-engine post can be published from the Command Center")
        connection.execute("UPDATE social_posts SET scheduled_at=?,day_key=?,status='READY' WHERE post_id=?",
                           (at.isoformat(), at.date().isoformat(), post_id))
        connection.execute("UPDATE social_opportunities SET scheduled_at=?,status='READY',updated_at=? WHERE opportunity_id=?",
                           (at.isoformat(), at.isoformat(), row["opportunity_id"]))
    return social.publish_one(post_id, clock)


def reschedule(opportunity_id: str, scheduled_at: str) -> dict[str, Any]:
    _setup()
    with social.connection() as connection:
        result = scheduler.schedule(connection, opportunity_id, scheduled_at)
        connection.execute("UPDATE social_posts SET scheduled_at=?,day_key=? WHERE post_id IN (SELECT post_id FROM social_post_details WHERE opportunity_id=?)",
                           (result["scheduled_at"], result["scheduled_at"][:10], opportunity_id))
        return result


def cancel(opportunity_id: str) -> dict[str, Any]:
    _setup()
    with social.connection() as connection:
        return scheduler.cancel(connection, opportunity_id)


def analytics_report() -> dict[str, Any]:
    _setup()
    with social.connection() as connection:
        return analytics.report(connection)


def engagement_queue(limit: int = 50) -> dict[str, Any]:
    _setup()
    with social.connection() as connection:
        rows = [dict(row) for row in connection.execute("""SELECT engagement_id,kind,x_post_id,conversation_id,
            game_id,candidate_text,status,reason,scheduled_at,reply_x_post_id,created_at,updated_at
            FROM social_engagement_queue ORDER BY created_at DESC LIMIT ?""", (max(1, min(limit, 100)),)).fetchall()]
    return {"items": rows, "total": len(rows), "autoRepliesEnabled": get_settings()["external_auto_replies_enabled"]}


def refresh_metrics(client_factory=OfficialX) -> dict[str, Any]:
    _setup(); client = client_factory(); client.verify_company()
    with social.connection() as connection:
        return analytics.collect(connection, client, social.sha)
