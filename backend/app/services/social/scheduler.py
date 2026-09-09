"""Resumable opportunity queue and post materialization."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
import os
import re
from typing import Any

from . import content
from .storage import decoded, json_value


def _load_opportunity(connection, opportunity_id: str) -> dict[str, Any]:
    row = connection.execute("""SELECT o.*,e.event_type,e.source_id,e.evidence_json,e.evidence_hash
        FROM social_opportunities o JOIN social_events e ON e.event_id=o.event_id
        WHERE o.opportunity_id=?""", (opportunity_id,)).fetchone()
    if not row:
        raise ValueError("Social opportunity not found")
    value = dict(row); value["evidence"] = decoded(value.pop("evidence_json"), {})
    return value


def materialize(connection, opportunity_id: str, *, sha, sign, cta: str = "", clock=None,
                writer_factory=content.OpenAICopyWriter) -> dict[str, Any]:
    at = (clock or (lambda: datetime.now(timezone.utc)))().astimezone(timezone.utc)
    opportunity = _load_opportunity(connection, opportunity_id)
    if opportunity["status"] not in {"QUEUED", "REVIEW", "DRAFT", "READY"}:
        raise ValueError("Opportunity cannot be materialized in its current state")
    existing = connection.execute("SELECT post_id FROM social_post_details WHERE opportunity_id=?", (opportunity_id,)).fetchone()
    if existing:
        return dict(connection.execute("SELECT * FROM social_posts WHERE post_id=?", (existing["post_id"],)).fetchone())
    event = {"event_id": opportunity["event_id"], "event_type": opportunity["event_type"], "evidence": opportunity["evidence"]}
    context = content.context_from_event(event, opportunity["content_type"], cta)
    caption, writer = content.compose(context, writer_factory)
    normalized = re.sub(r"\s+", " ", re.sub(r"https://\S+|\d+(?:\.\d+)?", "", caption.lower())).strip()
    for prior in connection.execute("SELECT content FROM social_posts ORDER BY scheduled_at DESC LIMIT 12").fetchall():
        prior_normalized = re.sub(r"\s+", " ", re.sub(r"https://\S+|\d+(?:\.\d+)?", "", prior["content"].lower())).strip()
        if SequenceMatcher(None, normalized, prior_normalized).ratio() >= .88:
            raise ValueError("Near-duplicate content blocked; editorial review required")
    post_id = sha({"opportunity_id": opportunity_id, "content_hash": sha(caption)})[:32]
    campaign = os.getenv("SOCIAL_CAMPAIGN", "smartbets-social-engine-v2")
    scheduled = opportunity.get("scheduled_at") or at.isoformat()
    day_key = datetime.fromisoformat(str(scheduled).replace("Z", "+00:00")).date().isoformat()
    signed = {"post_id": post_id, "content": caption, "source_id": opportunity["source_id"],
              "opportunity_id": opportunity_id, "source_hash": opportunity["source_hash"]}
    integrity_hash = sha({"signed": signed, "context": context})
    connection.execute("""INSERT INTO social_posts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(post_id) DO NOTHING""", (
        post_id, campaign, f"{opportunity['content_type']}|engine-v2", caption, opportunity["source_id"],
        at.isoformat(), scheduled, None, None, "READY", None, sha(caption), sign(signed), day_key,
    ))
    connection.execute("""INSERT INTO social_post_details(
        post_id,event_id,opportunity_id,game_id,post_type,media_asset_ids_json,media_type,
        score,score_reasons_json,source_context_json,source_hash,integrity_hash,created_at,updated_at)
        VALUES(?,?,?,?,?,'[]','text',?,?,?,?,?,?,?) ON CONFLICT(post_id) DO NOTHING""", (
        post_id, opportunity["event_id"], opportunity_id, opportunity.get("game_id"), opportunity["content_type"],
        opportunity["score"], opportunity["score_reasons_json"], json_value(context), opportunity["source_hash"],
        integrity_hash, at.isoformat(), at.isoformat(),
    ))
    connection.execute("UPDATE social_opportunities SET status='READY',updated_at=? WHERE opportunity_id=?",
                       (at.isoformat(), opportunity_id))
    post = dict(connection.execute("SELECT * FROM social_posts WHERE post_id=?", (post_id,)).fetchone())
    return {**post, "writer": writer, "context": context}


def schedule(connection, opportunity_id: str, scheduled_at: str, clock=None) -> dict[str, Any]:
    at = (clock or (lambda: datetime.now(timezone.utc)))().astimezone(timezone.utc)
    scheduled = datetime.fromisoformat(str(scheduled_at).replace("Z", "+00:00"))
    if scheduled.tzinfo is None or scheduled.astimezone(timezone.utc) < at:
        raise ValueError("Scheduled time must be timezone-aware and in the future")
    connection.execute("UPDATE social_opportunities SET status='QUEUED',scheduled_at=?,updated_at=? WHERE opportunity_id=?",
                       (scheduled.astimezone(timezone.utc).isoformat(), at.isoformat(), opportunity_id))
    row = connection.execute("SELECT * FROM social_opportunities WHERE opportunity_id=?", (opportunity_id,)).fetchone()
    if not row:
        raise ValueError("Social opportunity not found")
    return dict(row)


def cancel(connection, opportunity_id: str, reason: str = "OWNER_CANCELLED", clock=None) -> dict[str, Any]:
    at = (clock or (lambda: datetime.now(timezone.utc)))().astimezone(timezone.utc).isoformat()
    row = connection.execute("SELECT status FROM social_opportunities WHERE opportunity_id=?", (opportunity_id,)).fetchone()
    if not row:
        raise ValueError("Social opportunity not found")
    if row["status"] in {"PUBLISHED", "PUBLISHING", "UNKNOWN"}:
        raise ValueError("A delivered or ambiguous post cannot be cancelled")
    connection.execute("UPDATE social_opportunities SET status='SKIPPED',skip_reason=?,updated_at=? WHERE opportunity_id=?",
                       (reason[:160], at, opportunity_id))
    connection.execute("UPDATE social_posts SET status='CANCELLED',failure_reason=? WHERE post_id IN "
                       "(SELECT post_id FROM social_post_details WHERE opportunity_id=?)", (reason[:160], opportunity_id))
    return {"opportunity_id": opportunity_id, "status": "SKIPPED", "reason": reason[:160]}


def due(connection, settings: dict[str, Any], clock=None, limit: int = 2) -> list[dict[str, Any]]:
    at = (clock or (lambda: datetime.now(timezone.utc)))().astimezone(timezone.utc)
    if settings["paused"] or not settings["automation_enabled"]:
        return []
    last = connection.execute("SELECT published_at FROM social_posts WHERE status='PUBLISHED' AND published_at IS NOT NULL ORDER BY published_at DESC LIMIT 1").fetchone()
    if last:
        last_at = datetime.fromisoformat(str(last["published_at"]).replace("Z", "+00:00")).astimezone(timezone.utc)
        if at - last_at < timedelta(minutes=int(settings["min_post_interval_minutes"])):
            return []
    published = connection.execute("SELECT COUNT(*) AS count FROM social_posts WHERE status='PUBLISHED' AND day_key=?",
                                   (at.date().isoformat(),)).fetchone()["count"]
    remaining = max(0, int(settings["max_posts_per_day"]) - int(published))
    if not remaining:
        return []
    # A positive interval is also a between-items guarantee. Selecting a batch
    # of two here would publish both before the next invocation can observe the
    # first timestamp, defeating the owner's cadence control.
    batch_limit = 1 if int(settings["min_post_interval_minutes"]) > 0 else max(1, int(limit))
    receipt_count = int(connection.execute("""SELECT COUNT(*) AS count FROM social_posts p JOIN social_post_details d USING(post_id)
        WHERE p.status='PUBLISHED' AND p.day_key=? AND d.post_type IN ('WIN_RECEIPT','LOSS_RECEIPT','CASHED','MISS')""",
        (at.date().isoformat(),)).fetchone()["count"])
    rows = connection.execute("""SELECT * FROM social_opportunities WHERE status='QUEUED'
        AND scheduled_at<=? ORDER BY score DESC,scheduled_at LIMIT ?""",
        (at.isoformat(), min(batch_limit * 2, remaining * 2))).fetchall()
    selected = []
    for row in rows:
        if row["content_type"] in {"WIN_RECEIPT", "LOSS_RECEIPT", "CASHED", "MISS"}:
            if receipt_count >= int(settings["max_result_receipts_per_day"]):
                continue
            receipt_count += 1
        selected.append(dict(row))
        if len(selected) >= min(batch_limit, remaining):
            break
    return selected


def edit_caption(connection, post_id: str, caption: str, sha, sign, clock=None) -> dict[str, Any]:
    at = (clock or (lambda: datetime.now(timezone.utc)))().astimezone(timezone.utc).isoformat()
    row = connection.execute("""SELECT p.*,d.source_context_json,d.opportunity_id,d.source_hash
        FROM social_posts p JOIN social_post_details d USING(post_id) WHERE p.post_id=?""", (post_id,)).fetchone()
    if not row or row["status"] not in {"READY", "DRAFT", "REVIEW"}:
        raise ValueError("Only an unpublished social post can be edited")
    context = decoded(row["source_context_json"], {})
    caption = content.validate_caption(caption, context)
    signed = {"post_id": post_id, "content": caption, "source_id": row["source_id"],
              "opportunity_id": row["opportunity_id"], "source_hash": row["source_hash"]}
    integrity_hash = sha({"signed": signed, "context": context})
    connection.execute("UPDATE social_posts SET content=?,content_hash=?,signature=? WHERE post_id=?",
                       (caption, sha(caption), sign(signed), post_id))
    connection.execute("UPDATE social_post_details SET integrity_hash=?,owner_edited=1,updated_at=? WHERE post_id=?",
                       (integrity_hash, at, post_id))
    return {"post_id": post_id, "content": caption, "status": row["status"], "updated_at": at}
