"""Information-time contract for future pregame features."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable


class TemporalSafetyError(ValueError):
    pass


def parse_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def validate_feature_cutoff(source_events: Iterable[dict[str, Any]], forecast_cutoff: Any) -> None:
    """Reject a derived pregame feature if any dependency is not strictly prior."""
    cutoff = parse_timestamp(forecast_cutoff)
    if cutoff is None:
        raise TemporalSafetyError("forecast cutoff is missing or invalid")
    for index, event in enumerate(source_events):
        timestamp = parse_timestamp(event.get("information_time") or event.get("completed_at")
                                    or event.get("data_as_of") or event.get("captured_at"))
        if timestamp is None:
            raise TemporalSafetyError(f"source event {index} has unverified information time")
        if timestamp >= cutoff:
            raise TemporalSafetyError(
                f"source event {index} at {timestamp.isoformat()} is not strictly before cutoff {cutoff.isoformat()}"
            )
