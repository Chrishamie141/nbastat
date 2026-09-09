"""Typed environment defaults with owner-editable, non-secret overrides."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any


SETTING_SPECS: dict[str, tuple[str, Any, str | None]] = {
    "automation_enabled": ("bool", True, "SOCIAL_AUTOMATION_ENABLED"),
    "paused": ("bool", False, None),
    "dry_run": ("bool", True, "SOCIAL_DRY_RUN"),
    "auto_publish": ("bool", False, "SOCIAL_AUTO_PUBLISH"),
    "ai_copy_enabled": ("bool", False, "SOCIAL_AI_COPY_ENABLED"),
    "ai_images_enabled": ("bool", False, "SOCIAL_AI_IMAGES_ENABLED"),
    "image_quality": ("choice", "high", "SOCIAL_IMAGE_QUALITY"),
    "max_images_per_day": ("int", 3, "SOCIAL_MAX_IMAGES_PER_DAY"),
    "video_enabled": ("bool", False, "SOCIAL_VIDEO_ENABLED"),
    "max_videos_per_day": ("int", 1, "SOCIAL_MAX_VIDEOS_PER_DAY"),
    "result_receipts_enabled": ("bool", True, "SOCIAL_RESULT_RECEIPTS_ENABLED"),
    "line_movement_enabled": ("bool", True, "SOCIAL_LINE_MOVEMENT_ENABLED"),
    "daily_recap_enabled": ("bool", True, "SOCIAL_DAILY_RECAP_ENABLED"),
    "weekly_recap_enabled": ("bool", True, "SOCIAL_WEEKLY_RECAP_ENABLED"),
    "engagement_questions_enabled": ("bool", True, "SOCIAL_ENGAGEMENT_QUESTIONS_ENABLED"),
    "external_auto_replies_enabled": ("bool", False, "SOCIAL_AUTO_REPLIES_ENABLED"),
    "opportunity_score_threshold": ("float", 60.0, "SOCIAL_OPPORTUNITY_SCORE_THRESHOLD"),
    "min_post_interval_minutes": ("int", 90, "SOCIAL_MIN_POST_INTERVAL_MINUTES"),
    "max_posts_per_day": ("int", 4, "SOCIAL_MAX_POSTS_PER_DAY"),
    "max_result_receipts_per_day": ("int", 2, "SOCIAL_MAX_RESULT_RECEIPTS_PER_DAY"),
    "live_reactions_enabled": ("bool", False, "SOCIAL_LIVE_REACTIONS_ENABLED"),
}

PUBLIC_SETTING_KEYS = frozenset(SETTING_SPECS)


def _coerce(kind: str, value: Any) -> Any:
    if kind == "bool":
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
            raise ValueError("Expected an on/off setting")
        return normalized in {"true", "1", "yes", "on"}
    if kind == "int":
        value = int(value)
        if not 0 <= value <= 10_000:
            raise ValueError("Setting is outside its supported range")
        return value
    if kind == "float":
        value = float(value)
        if not 0 <= value <= 100:
            raise ValueError("Setting is outside its supported range")
        return round(value, 2)
    if kind == "choice":
        value = str(value).lower()
        if value not in {"low", "medium", "high"}:
            raise ValueError("Unsupported image quality")
        return value
    raise ValueError("Unsupported setting type")


def environment_defaults() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, (kind, fallback, env_name) in SETTING_SPECS.items():
        raw = os.getenv(env_name) if env_name else None
        if key == "dry_run" and raw in (None, ""):
            raw = os.getenv("DRY_RUN")
        result[key] = _coerce(kind, fallback if raw in (None, "") else raw)
    return result


def load_settings(connection) -> dict[str, Any]:
    result = environment_defaults()
    rows = connection.execute("SELECT setting_key,value_json FROM social_settings").fetchall()
    for row in rows:
        key = row["setting_key"]
        if key in SETTING_SPECS:
            result[key] = _coerce(SETTING_SPECS[key][0], json.loads(row["value_json"]))
    return result


def update_settings(connection, updates: dict[str, Any], actor: str) -> dict[str, Any]:
    unknown = set(updates) - PUBLIC_SETTING_KEYS
    if unknown:
        raise ValueError("Unsupported social setting: " + ", ".join(sorted(unknown)))
    at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for key, raw in updates.items():
        value = _coerce(SETTING_SPECS[key][0], raw)
        connection.execute(
            "INSERT INTO social_settings(setting_key,value_json,updated_at,updated_by) VALUES(?,?,?,?) "
            "ON CONFLICT(setting_key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at,updated_by=excluded.updated_by",
            (key, json.dumps(value), at, actor),
        )
    return load_settings(connection)
