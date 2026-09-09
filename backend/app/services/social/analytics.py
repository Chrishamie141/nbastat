"""Real X metric snapshots and null-aware performance aggregation."""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import statistics
import uuid
from typing import Any

from .storage import decoded, json_value


METRICS = ("impressions", "likes", "replies", "reposts", "quotes", "bookmarks", "url_clicks", "profile_clicks", "followers")
logger = logging.getLogger(__name__)


def store_snapshot(connection, post_id: str, x_post_id: str | None, values: dict[str, Any], sha, clock=None) -> dict[str, Any]:
    at = (clock or (lambda: datetime.now(timezone.utc)))().astimezone(timezone.utc).replace(microsecond=0).isoformat()
    cleaned = {key: (float(values[key]) if values.get(key) is not None else None) for key in METRICS}
    availability = {key: cleaned[key] is not None for key in METRICS}
    metric_id = uuid.uuid4().hex
    connection.execute("""INSERT INTO social_metrics(metric_id,post_id,x_post_id,collected_at,impressions,likes,replies,
        reposts,quotes,bookmarks,url_clicks,profile_clicks,followers,availability_json,raw_hash)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        metric_id, post_id, x_post_id, at, *(cleaned[key] for key in METRICS), json_value(availability), sha(cleaned),
    ))
    return {"metric_id": metric_id, "post_id": post_id, "collected_at": at, **cleaned, "availability": availability}


def _engagement_rate(row: dict[str, Any]) -> float | None:
    impressions = row.get("impressions")
    available = [row.get(key) for key in ("likes", "replies", "reposts", "quotes")]
    if impressions in (None, 0) or any(value is None for value in available):
        return None
    return round(sum(available) / impressions, 6)


def report(connection) -> dict[str, Any]:
    snapshots = [dict(row) for row in connection.execute("""SELECT m.*,d.post_type,d.media_type,
        (SELECT a.template FROM social_media_assets a WHERE a.post_id=m.post_id ORDER BY a.version DESC LIMIT 1) AS template
        FROM social_metrics m LEFT JOIN social_post_details d USING(post_id)
        WHERE m.collected_at=(SELECT MAX(m2.collected_at) FROM social_metrics m2 WHERE m2.post_id=m.post_id)""").fetchall()]
    for row in snapshots:
        row["engagement_rate"] = _engagement_rate(row)
    def aggregate(field: str) -> list[dict[str, Any]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in snapshots:
            groups.setdefault(str(row.get(field) or "legacy"), []).append(row)
        result = []
        for key, group in sorted(groups.items()):
            impressions = [row["impressions"] for row in group if row.get("impressions") is not None]
            rates = [row["engagement_rate"] for row in group if row.get("engagement_rate") is not None]
            result.append({"name": key, "sampleSize": len(group),
                           "averageImpressions": round(statistics.mean(impressions), 2) if impressions else None,
                           "medianImpressions": round(statistics.median(impressions), 2) if impressions else None,
                           "engagementRate": round(statistics.mean(rates), 6) if rates else None,
                           "sampleStatus": "SUFFICIENT" if len(group) >= 10 else "INSUFFICIENT_SAMPLE"})
        return result
    totals = {}
    for key in METRICS:
        values = [row[key] for row in snapshots if row.get(key) is not None]
        totals[key] = sum(values) if values else None
    followers = [(row["collected_at"], row["followers"]) for row in snapshots if row.get("followers") is not None]
    totals["followers"] = sorted(followers)[-1][1] if followers else None
    follower_growth = (sorted(followers)[-1][1] - sorted(followers)[0][1]) if len(followers) > 1 else None
    best = sorted((row for row in snapshots if row.get("impressions") is not None), key=lambda row: row["impressions"], reverse=True)[:5]
    return {"postsWithMetrics": len(snapshots), "totals": totals,
            "byPostType": aggregate("post_type"), "byMediaType": aggregate("media_type"),
            "byTemplate": aggregate("template"), "bestPerformingPosts": [
                {"postId": row["post_id"], "impressions": row["impressions"], "engagementRate": row["engagement_rate"]} for row in best],
            "followerGrowth": follower_growth,
            "availabilityMessage": "Not available from current X API access"}


def collect(connection, client, sha, clock=None, limit: int = 20) -> dict[str, Any]:
    posts = connection.execute("SELECT post_id,x_post_id FROM social_posts WHERE status='PUBLISHED' AND x_post_id IS NOT NULL ORDER BY published_at DESC LIMIT ?", (max(1, min(limit, 50)),)).fetchall()
    stored = []
    failures = []
    for post in posts:
        try:
            values = client.metrics(post["x_post_id"])
            stored.append(store_snapshot(connection, post["post_id"], post["x_post_id"], values, sha, clock))
        except Exception as exc:
            code = str(exc) if str(exc).startswith("X_") else type(exc).__name__.upper()
            failures.append({"postId": post["post_id"], "errorCode": code[:80]})
            logger.warning("social_metrics_unavailable", extra={"postId": post["post_id"], "errorCode": code[:80]})
    return {"collected": len(stored), "failed": len(failures), "failures": failures, "snapshots": stored}
