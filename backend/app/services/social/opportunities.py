"""Event-to-content mapping and persistent opportunity decisions."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from . import events
from .models import OpportunityDecision
from .scoring import score
from .storage import json_value


EVENT_CONTENT = {
    "SLATE_READY": "TODAYS_CARD",
    "PREDICTION_READY": "GAME_PREVIEW",
    "HIGH_CONFIDENCE_PREDICTION": "AI_PICK",
    "MODEL_MARKET_DISAGREEMENT": "MODEL_VS_MARKET",
    "LINE_MOVEMENT": "LINE_MOVEMENT",
    "GAME_STARTING_SOON": "GAME_PREVIEW",
    "PRIMETIME_GAME": "ENGAGEMENT_QUESTION",
    "UPSET_WATCH": "UPSET_WATCH",
    "PREDICTION_WIN": "WIN_RECEIPT",
    "PREDICTION_LOSS": "LOSS_RECEIPT",
    "PREDICTION_PUSH": "DAILY_RECAP",
    "DAILY_SLATE_COMPLETE": "DAILY_RECAP",
    "WEEKLY_SLATE_COMPLETE": "WEEKLY_REPORT",
    "STREAK_MILESTONE": "STREAK_MILESTONE",
}


def _analytics_modifier(connection, content_type: str) -> float:
    """A deliberately small input, only after ten real measured posts."""
    rows = connection.execute("""SELECT m.impressions,m.likes,m.replies,m.reposts,m.quotes,d.post_type
        FROM social_metrics m JOIN social_post_details d USING(post_id)
        WHERE m.impressions>0 AND m.likes IS NOT NULL AND m.replies IS NOT NULL AND m.reposts IS NOT NULL
        AND m.collected_at=(SELECT MAX(m2.collected_at) FROM social_metrics m2 WHERE m2.post_id=m.post_id)""").fetchall()
    target = [row for row in rows if row["post_type"] == content_type]
    if len(target) < 10:
        return 0
    def rate(row):
        return (float(row["likes"]) + float(row["replies"]) + float(row["reposts"]) + float(row["quotes"] or 0)) / float(row["impressions"])
    overall = sum(rate(row) for row in rows) / len(rows)
    specific = sum(rate(row) for row in target) / len(target)
    return 3 if specific > overall * 1.10 else -3 if specific < overall * .90 else 0


def discover(connection, source_id: str, source: dict[str, Any], settings: dict[str, Any], sha, clock) -> dict[str, Any]:
    at = clock().astimezone(timezone.utc)
    found = events.discover(source_id, source, clock)
    stored: list[dict[str, Any]] = []
    published_today = connection.execute(
        "SELECT COUNT(*) AS count FROM social_posts WHERE status='PUBLISHED' AND day_key=?", (at.date().isoformat(),)
    ).fetchone()["count"]
    for raw_event in found:
        event = events.store(connection, raw_event, sha)
        content_type = EVENT_CONTENT.get(event["event_type"])
        if not content_type:
            continue
        same = connection.execute("""SELECT 1 FROM social_post_details d JOIN social_posts p ON p.post_id=d.post_id
            WHERE COALESCE(d.game_id,'')=COALESCE(?,'') AND d.post_type=? AND p.status IN ('PUBLISHED','PUBLISHING','UNKNOWN') LIMIT 1""",
            (event.get("game_id"), content_type)).fetchone() is not None
        result = score(event, settings, recent_posts=int(published_today), same_game_type_posted=same,
                       analytics_modifier=_analytics_modifier(connection, content_type), clock=clock)
        if event["event_type"] in {"PREDICTION_WIN", "PREDICTION_LOSS", "PREDICTION_PUSH"} and not settings["result_receipts_enabled"]:
            result = type(result)(result.score, result.threshold, OpportunityDecision.SKIP,
                                  result.reasons + ("Result receipts are disabled",))
        if event["event_type"] == "LINE_MOVEMENT" and not settings["line_movement_enabled"]:
            result = type(result)(result.score, result.threshold, OpportunityDecision.SKIP,
                                  result.reasons + ("Line movement posts are disabled",))
        if event["event_type"] == "DAILY_SLATE_COMPLETE" and not settings["daily_recap_enabled"]:
            result = type(result)(result.score, result.threshold, OpportunityDecision.SKIP,
                                  result.reasons + ("Daily recap posts are disabled",))
        if event["event_type"] == "WEEKLY_SLATE_COMPLETE" and not settings["weekly_recap_enabled"]:
            result = type(result)(result.score, result.threshold, OpportunityDecision.SKIP,
                                  result.reasons + ("Weekly recap posts are disabled",))
        if content_type == "ENGAGEMENT_QUESTION" and not settings["engagement_questions_enabled"]:
            result = type(result)(result.score, result.threshold, OpportunityDecision.SKIP,
                                  result.reasons + ("Engagement questions are disabled",))
        identity = sha({"event_id": event["event_id"], "content_type": content_type})
        opportunity_id = identity[:32]
        status = {
            OpportunityDecision.SKIP: "SKIPPED", OpportunityDecision.REVIEW: "REVIEW",
            OpportunityDecision.POST_NOW: "QUEUED", OpportunityDecision.QUEUE: "QUEUED",
        }[result.decision]
        scheduled = at if result.decision == OpportunityDecision.POST_NOW else at + timedelta(minutes=15)
        skip_reason = "; ".join(result.reasons[-2:]) if status == "SKIPPED" else None
        connection.execute("""INSERT INTO social_opportunities(
            opportunity_id,event_id,game_id,content_type,score,threshold,decision,score_reasons_json,
            idempotency_key,source_hash,status,scheduled_at,created_at,updated_at,skip_reason)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(idempotency_key) DO NOTHING""", (
            opportunity_id, event["event_id"], event.get("game_id"), content_type, result.score,
            result.threshold, result.decision.value, json_value(list(result.reasons)), identity,
            event["evidence_hash"], status, scheduled.isoformat() if status != "SKIPPED" else None,
            at.isoformat(), at.isoformat(), skip_reason,
        ))
        stored.append({"opportunity_id": opportunity_id, "event_id": event["event_id"],
                       "game_id": event.get("game_id"), "content_type": content_type,
                       "status": status, **result.as_dict()})
    return {
        "discovered": len(found), "opportunities": len(stored),
        "queued": sum(item["status"] == "QUEUED" for item in stored),
        "review": sum(item["status"] == "REVIEW" for item in stored),
        "skipped": sum(item["status"] == "SKIPPED" for item in stored),
        "items": stored,
    }
