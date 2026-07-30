"""Shared schema contract for leakage-safe NFL team history."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .game_matching import normalize_team, parse_dt

COMPLETED_GAME_HISTORY = "completed_game_history"
PREGAME_AGGREGATE = "pregame_history"
VALID_TEAM_HISTORY_ROLES = frozenset({COMPLETED_GAME_HISTORY, PREGAME_AGGREGATE})


def prediction_cutoff(game: dict[str, Any]):
    """Return the replay cutoff for a game (all feature times are strict).

    Snapshot builders may freeze a game earlier than kickoff.  In that case the
    explicit cutoff wins; older snapshots use kickoff, which is the replay
    engine's longstanding pregame boundary.
    """
    return parse_dt(game.get("prediction_cutoff") or game.get("prediction_timestamp")
                    or game.get("kickoff_time") or game.get("commence_time"))


def prediction_cutoff_source(game: dict[str, Any]) -> str | None:
    """Identify the authoritative cutoff field, in priority order."""
    for field in ("prediction_cutoff", "prediction_timestamp"):
        if game.get(field) not in (None, ""):
            return field
    if game.get("kickoff_time") or game.get("commence_time"):
        return "kickoff_fallback"
    return None


def history_known_at(row: dict[str, Any]):
    """Return when a historical fact was provably available.

    Completion, capture, and data-as-of are all constraints, so the latest is
    authoritative.  A supplied malformed constraint makes eligibility
    unprovable rather than allowing fallback to an earlier field.
    """
    values = []
    for field in ("completed_at", "captured_at", "data_as_of"):
        value = row.get(field)
        if value not in (None, ""):
            parsed = parse_dt(value)
            if parsed is None:
                return None
            values.append(parsed)
    return max(values) if values else None


@dataclass(frozen=True)
class HistoryFilterResult:
    rows: list[dict[str, Any]]
    loaded: int
    rejected_future: int
    rejected_unknown_timestamp: int
    rejected_other: int
    latest_timestamp: str | None
    rejected_rows: list[dict[str, Any]]


def filter_game_history(game: dict[str, Any], rows: list[dict[str, Any]], *, dataset: str,
                        target_teams_only: bool = True) -> HistoryFilterResult:
    """Select leakage-safe history, optionally restricted to target participants."""
    cutoff = prediction_cutoff(game)
    if cutoff is None:
        raise ValueError("target game has no valid prediction cutoff")
    teams = {normalize_team(game.get("home_team")), normalize_team(game.get("away_team"))}
    target_id = str(game.get("game_id") or game.get("id") or "")
    eligible, rejected, future = [], [], 0
    unknown = other = 0
    latest = None
    for row in rows:
        known = history_known_at(row)
        reason = None
        if known is None:
            reason = "unknown_timestamp"; unknown += 1
        elif known >= cutoff:
            reason = "future"; future += 1
        elif target_id and str(row.get("game_id") or "") == target_id:
            reason = "target_game"; other += 1
        elif target_teams_only and normalize_team(row.get("team")) not in teams:
            reason = "irrelevant_team"; other += 1
        elif dataset == "team" and row.get("record_role", PREGAME_AGGREGATE) not in VALID_TEAM_HISTORY_ROLES:
            reason = "invalid_record_role"; other += 1
        elif dataset == "team" and row.get("record_role", PREGAME_AGGREGATE) == COMPLETED_GAME_HISTORY and row.get("is_pregame") is not False:
            reason = "not_completed_history"; other += 1
        elif dataset == "team" and row.get("record_role", PREGAME_AGGREGATE) == PREGAME_AGGREGATE and not row.get("is_pregame", True):
            reason = "invalid_pregame_aggregate"; other += 1
        if reason:
            if reason in {"future", "unknown_timestamp", "target_game"}:
                rejected.append({**row, "rejection_reason": reason})
            continue
        eligible.append(row)
        latest = known if latest is None or known > latest else latest
    return HistoryFilterResult(eligible, len(rows), future, unknown, other,
                               latest.isoformat().replace("+00:00", "Z") if latest else None, rejected)


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
