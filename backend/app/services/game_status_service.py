from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any

CANONICAL_STATUSES = {
    "scheduled",
    "pregame",
    "live",
    "halftime",
    "final",
    "final-OT",
    "postponed",
    "canceled",
    "unknown",
}
FINAL_STATUSES = {"final", "final-OT"}


def _text(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", " ").replace("-", " ")


def normalize_game_status(provider_status: Any, detail: Any = None, completed: bool = False) -> str:
    """Map provider-specific status values to the single public lifecycle vocabulary."""
    joined = f"{_text(provider_status)} {_text(detail)}".strip()
    if "cancel" in joined:
        return "canceled"
    if "postpon" in joined or "delay" in joined:
        return "postponed"
    if completed or "final" in joined or "completed" in joined:
        overtime = "overtime" in joined or bool(re.search(r"(?:^|[\s/])ot(?:$|[\s/])", joined))
        return "final-OT" if overtime else "final"
    if "half" in joined:
        return "halftime"
    if any(token in joined for token in ("in progress", "status in", "live", "quarter", "end period")):
        return "live"
    if "pregame" in joined or "pre game" in joined or joined in {"pre", "status pre"}:
        return "pregame"
    if any(token in joined for token in ("scheduled", "status scheduled")):
        return "scheduled"
    return "unknown"


def parse_provider_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if value:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def monotonic_status(previous: str | None, incoming: str, previous_at: Any = None, incoming_at: Any = None) -> str:
    """Keep a confirmed final state from regressing when an older provider response arrives."""
    if previous in FINAL_STATUSES and incoming not in FINAL_STATUSES:
        old_time = parse_provider_timestamp(previous_at)
        new_time = parse_provider_timestamp(incoming_at)
        if new_time <= old_time:
            return previous
        # FINAL is authoritative even when a provider later emits a stale non-final label.
        return previous
    if previous == "final-OT" and incoming == "final":
        return previous
    return incoming if incoming in CANONICAL_STATUSES else "unknown"


def cache_ttl_seconds(status: str) -> int:
    if status in {"live", "halftime"}:
        return 15
    if status in {"pregame", "scheduled"}:
        return 60 if status == "pregame" else 300
    if status in FINAL_STATUSES or status in {"canceled", "postponed"}:
        return 86400
    return 120


def lifecycle_cache_ttl(status: str, kickoff_at: Any = None, now_at: Any = None, stats_complete: bool = True) -> int:
    """Choose an upstream TTL from lifecycle, kickoff proximity, and final-stat completeness."""
    now = parse_provider_timestamp(now_at)
    kickoff = parse_provider_timestamp(kickoff_at) if kickoff_at else None
    if status in {"live", "halftime"}:
        return 15
    if status == "pregame":
        return 60
    if status == "scheduled":
        until_kickoff = kickoff - now if kickoff else timedelta(hours=24)
        if until_kickoff > timedelta(hours=24):
            return 1800
        if until_kickoff <= timedelta(hours=1):
            return 60
        return 300
    if status in FINAL_STATUSES:
        if not stats_complete:
            return 60
        if kickoff and now - kickoff < timedelta(hours=24):
            return 300
        return 86400
    return cache_ttl_seconds(status)
