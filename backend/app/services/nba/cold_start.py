"""Leakage-safe NBA early-season feature construction."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


def _dt(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_cold_start_features(
    rows: list[dict], *, team: str, cutoff: str, current_season: str,
    prior_season: str, current_weight_cap: float = 0.8,
) -> dict:
    """Blend prior-season and current-season history available before cutoff.

    Current-season weight grows by 10 percentage points per completed game and
    is capped so October/November predictions retain a stable prior.
    """
    boundary = _dt(cutoff)
    eligible = [row for row in rows if row.get("team") == team and row.get("completed_at")
                and _dt(row["completed_at"]) < boundary
                and row.get("season") in {current_season, prior_season}]
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in eligible:
        grouped[str(row["season"])].append(row)
    current = sorted(grouped[current_season], key=lambda row: row["completed_at"])
    prior = sorted(grouped[prior_season], key=lambda row: row["completed_at"])[-20:]
    current_weight = min(current_weight_cap, len(current) / 10.0)
    prior_weight = 1.0 - current_weight
    numeric = sorted({key for row in current + prior for key, value in row.items()
                      if isinstance(value, (int, float)) and key not in {"season"}})
    features = {}
    for key in numeric:
        current_values = [float(row[key]) for row in current if isinstance(row.get(key), (int, float))]
        prior_values = [float(row[key]) for row in prior if isinstance(row.get(key), (int, float))]
        current_avg = sum(current_values) / len(current_values) if current_values else None
        prior_avg = sum(prior_values) / len(prior_values) if prior_values else None
        if current_avg is None and prior_avg is None:
            continue
        if current_avg is None:
            value = prior_avg
        elif prior_avg is None:
            value = current_avg
        else:
            value = current_avg * current_weight + prior_avg * prior_weight
        features[key] = round(float(value), 6)
    return {
        "team": team, "cutoff": cutoff, "current_season": current_season,
        "prior_season": prior_season, "current_games": len(current),
        "prior_games": len(prior), "current_weight": current_weight,
        "prior_weight": prior_weight, "features": features,
        "status": "READY" if prior or current else "INSUFFICIENT_HISTORY",
        "provenance": {"cutoffRule": "completed_at_strictly_before_prediction",
                       "priorSeasonWindow": 20, "currentSeasonWeightCap": current_weight_cap},
    }


def build_player_cold_start_features(
    rows: list[dict], *, player_id: str, cutoff: str, current_season: str,
    prior_season: str, current_weight_cap: float = .8,
) -> dict:
    """Player equivalent of the team cold-start foundation.

    Identity is provider player ID, not a mutable display name. Team changes
    remain visible in provenance and no roster fact is inferred when absent.
    """
    player_rows = [{**row, "team": str(player_id)} for row in rows
                   if str(row.get("player_id") or "") == str(player_id)]
    result = build_cold_start_features(
        player_rows, team=str(player_id), cutoff=cutoff,
        current_season=current_season, prior_season=prior_season,
        current_weight_cap=current_weight_cap,
    )
    teams = sorted({str(row["team_id"]) for row in rows
                    if str(row.get("player_id") or "") == str(player_id)
                    and row.get("team_id") and row.get("completed_at")
                    and _dt(row["completed_at"]) < _dt(cutoff)})
    return {**result, "player_id": str(player_id), "teamsObserved": teams,
            "rosterChangeStatus": "OBSERVED_MULTIPLE_TEAMS" if len(teams) > 1 else
                                  "SINGLE_TEAM" if teams else "UNAVAILABLE"}
