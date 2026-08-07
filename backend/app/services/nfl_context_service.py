from __future__ import annotations

import json
import logging
from pathlib import Path
from statistics import fmean
from typing import Any

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parents[3]
MINIMUM_SAMPLE_SIZE = 3


def _season_rows(season: int) -> tuple[list[dict[str, Any]], Path | None]:
    root = BASE_DIR / "backtesting" / "data" / "snapshots" / "nfl" / str(season)
    candidates = sorted(root.glob("week_*/team_stats.json"))
    combined: dict[tuple[str, str], dict[str, Any]] = {}
    for path in candidates:
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("nfl_context_artifact_unreadable", extra={"season": season, "path": str(path)})
            continue
        if isinstance(rows, list):
            for row in rows:
                key = (str(row.get("game_id") or ""), str(row.get("team") or ""))
                if all(key):
                    combined[key] = row
    if combined:
        return list(combined.values()), root
    runtime_path = BASE_DIR / "data" / "nfl_team_context_history.json"
    if runtime_path.exists():
        try:
            rows = json.loads(runtime_path.read_text(encoding="utf-8"))
            selected = [row for row in rows if int(row.get("season") or 0) == season]
            if selected:
                return selected, runtime_path
        except (OSError, json.JSONDecodeError, ValueError):
            logger.exception("nfl_runtime_context_unreadable", extra={"season": season})
    return [], None


def _team_summary(rows: list[dict[str, Any]], team: str, game_id: str) -> dict[str, Any] | None:
    eligible = [
        row for row in rows
        if row.get("team") == team
        and str(row.get("game_id", "")).removeprefix("espn-") != game_id
        and row.get("record_role") == "completed_game_history"
        and isinstance(row.get("points_for"), (int, float))
        and isinstance(row.get("points_against"), (int, float))
    ]
    if not eligible:
        return None
    eligible.sort(key=lambda row: (row.get("completed_at") or "", row.get("week") or 0))
    recent = eligible[-5:]
    wins = sum(row["points_for"] > row["points_against"] for row in recent)
    return {
        "team": team,
        "games": len(eligible),
        "metrics": {
            "pointsPerGame": round(fmean(row["points_for"] for row in eligible), 1),
            "pointsAllowedPerGame": round(fmean(row["points_against"] for row in eligible), 1),
            "recentFivePointsPerGame": round(fmean(row["points_for"] for row in recent), 1),
            "recentFiveWins": wins,
        },
    }


def build_team_context(game: dict[str, Any]) -> dict[str, Any]:
    """Return pregame-only context without ever substituting for game outcomes."""
    requested = int(game["season"])
    game_id = str(game["id"])
    teams = [game["awayTeam"]["abbreviation"], game["homeTeam"]["abbreviation"]]
    current_rows, current_path = _season_rows(requested)
    current = [_team_summary(current_rows, team, game_id) for team in teams]
    current_sizes = {team: (summary or {}).get("games", 0) for team, summary in zip(teams, current)}
    # Preseason is intentionally never blended into the regular-season
    # baseline. Its observations remain visible through actual game results.
    use_current = game.get("seasonPhase") != "preseason" and all(summary and summary["games"] >= MINIMUM_SAMPLE_SIZE for summary in current)

    selected = current
    context_season = requested
    source_path = current_path
    fallback_used = False
    reason = None
    if not use_current:
        previous_rows, previous_path = _season_rows(requested - 1)
        previous = [_team_summary(previous_rows, team, game_id) for team in teams]
        if all(previous):
            selected = previous
            context_season = requested - 1
            source_path = previous_path
            fallback_used = True
            reason = "INSUFFICIENT_CURRENT_SEASON_SAMPLE"

    available = all(selected)
    if fallback_used:
        logger.info("nfl_context_fallback %s", json.dumps({"gameId": game_id, "requestedSeason": requested, "contextSeasonUsed": context_season, "currentSeasonSampleSize": current_sizes, "reason": reason}, sort_keys=True))
    return {
        "available": available,
        "label": "PREGAME CONTEXT",
        "source": f"versioned completed-game snapshots ({context_season} regular season)" if source_path else "unavailable",
        "reason": None if available else "No sufficient completed-game context artifact is available for both teams.",
        "requestedSeason": requested,
        "contextSeasonUsed": context_season if available else None,
        "currentSeasonSampleSize": current_sizes,
        "minimumSampleSize": MINIMUM_SAMPLE_SIZE,
        "fallbackUsed": fallback_used,
        "fallbackReason": reason,
        "phasePolicy": "Regular-season completed games only; preseason observations are not merged.",
        "teams": [summary for summary in selected if summary],
    }
