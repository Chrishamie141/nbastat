"""Approval-first reply candidate infrastructure."""
from __future__ import annotations

from datetime import datetime, timezone
import uuid

from .content import validate_caption


def create_candidate(connection, *, kind: str, source_id: str, text: str, context: dict,
                     sha, x_post_id: str | None = None, conversation_id: str | None = None,
                     game_id: str | None = None, clock=None) -> dict:
    if kind not in {"DIRECT_MENTION", "EXTERNAL_CONVERSATION"}:
        raise ValueError("Unsupported engagement kind")
    caption = validate_caption(text, context)
    at = (clock or (lambda: datetime.now(timezone.utc)))().astimezone(timezone.utc).isoformat()
    engagement_id = uuid.uuid4().hex
    connection.execute("""INSERT INTO social_engagement_queue(
        engagement_id,kind,x_post_id,conversation_id,game_id,source_id,candidate_text,content_hash,
        status,reason,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(content_hash) DO NOTHING""",
        (engagement_id, kind, x_post_id, conversation_id, game_id, source_id, caption, sha(caption),
         "REVIEW", "External auto replies are off by default", at, at))
    return {"engagement_id": engagement_id, "status": "REVIEW", "candidate_text": caption, "kind": kind}


def decide(connection, engagement_id: str, approved: bool, clock=None) -> dict:
    at = (clock or (lambda: datetime.now(timezone.utc)))().astimezone(timezone.utc).isoformat()
    status = "APPROVED" if approved else "REJECTED"
    updated = connection.execute("UPDATE social_engagement_queue SET status=?,updated_at=? WHERE engagement_id=? AND status='REVIEW'",
                                 (status, at, engagement_id)).rowcount
    if not updated:
        raise ValueError("Reply candidate is not awaiting review")
    return {"engagement_id": engagement_id, "status": status, "updated_at": at}
