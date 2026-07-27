"""Shared schema contract for leakage-safe NFL team history."""

from __future__ import annotations

from typing import Any

from .game_matching import normalize_team, parse_dt

COMPLETED_GAME_HISTORY = "completed_game_history"
PREGAME_AGGREGATE = "pregame_history"
VALID_TEAM_HISTORY_ROLES = frozenset({COMPLETED_GAME_HISTORY, PREGAME_AGGREGATE})


def canonicalize_team_history(row: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize identifiers, scores, and UTC timestamps without losing zero scores."""
    result = dict(row)
    result["team"] = normalize_team(result.get("team"))
    if result.get("opponent") is not None:
        result["opponent"] = normalize_team(result["opponent"])
    for field in ("points_for", "points_against"):
        value = result.get(field)
        if value is not None:
            try:
                number = float(value)
                result[field] = int(number) if number.is_integer() else number
            except (TypeError, ValueError):
                pass
    for field in ("completed_at", "data_as_of", "captured_at"):
        if result.get(field) is not None:
            parsed = parse_dt(result[field])
            if parsed:
                result[field] = parsed.isoformat().replace("+00:00", "Z")
    return result
