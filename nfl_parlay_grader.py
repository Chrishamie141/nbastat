"""NFL parlay grading helpers for pending parlay history rows."""

from __future__ import annotations

import json
import re
from typing import Any

from nfl_data_service import get_nfl_final_player_stats, get_nfl_final_team_results
from prediction_storage import DB_FILE, get_connection, initialize_parlay_history, utc_now_iso

PLAYER_STAT_ALIASES = {
    "PASS_YDS": "PASS_YDS",
    "PASSING_YARDS": "PASS_YDS",
    "RUSH_YDS": "RUSH_YDS",
    "RUSHING_YARDS": "RUSH_YDS",
    "REC_YDS": "REC_YDS",
    "RECEIVING_YARDS": "REC_YDS",
    "RECEPTIONS": "RECEPTIONS",
    "ANYTIME_TD": "ANYTIME_TD",
    "TD": "ANYTIME_TD",
    "PASS_TDS": "PASS_TDS",
    "PASS_TD": "PASS_TDS",
    "PASS_INT": "PASS_INT",
    "PASSING_INTERCEPTIONS": "PASS_INT",
}
TEAM_MARKET_ALIASES = {
    "MONEYLINE": "MONEYLINE",
    "ML": "MONEYLINE",
    "SPREAD": "SPREAD",
    "TOTAL": "TOTAL",
    "TOTALS": "TOTAL",
}


def _normalize_name(name: str | None) -> str:
    return " ".join(str(name or "").lower().replace(".", "").split())


def _normalize_stat(stat_type: str | None) -> str:
    key = str(stat_type or "").strip().upper().replace(" ", "_")
    return PLAYER_STAT_ALIASES.get(key, key)


def _prediction_side(leg: dict[str, Any]) -> str:
    side = str(leg.get("side") or "").strip().lower()
    if side in {"over", "under"}:
        return side
    prediction = str(leg.get("prediction") or "").lower()
    under_match = re.search(r"\bunder\b", prediction)
    over_match = re.search(r"\bover\b", prediction)
    if under_match and (not over_match or under_match.start() < over_match.start()):
        return "under"
    return "over"


def _actual_player_value(leg: dict[str, Any], final_stats: dict[str, Any]) -> float | None:
    player = _normalize_name(leg.get("player"))
    stat = _normalize_stat(leg.get("stat_type"))
    for name, stats in final_stats.items():
        if _normalize_name(name) != player:
            continue
        normalized_stats = {_normalize_stat(key): value for key, value in dict(stats).items()}
        value = normalized_stats.get(stat)
        if value is None:
            return None
        if stat == "ANYTIME_TD":
            return 1.0 if float(value) > 0 else 0.0
        return float(value)
    return None


def _grade_player_leg(leg: dict[str, Any], final_stats: dict[str, Any]) -> str:
    actual = _actual_player_value(leg, final_stats)
    if actual is None or leg.get("line") is None:
        return "pending"
    line = float(leg["line"])
    side = _prediction_side(leg)
    return "hit" if (actual < line if side == "under" else actual > line) else "missed"


def _grade_team_leg(leg: dict[str, Any], team_results: dict[str, Any]) -> str:
    market = TEAM_MARKET_ALIASES.get(str(leg.get("stat_type") or leg.get("market") or "").upper())
    team = str(leg.get("team") or "").upper()
    if not market or not team:
        return "pending"
    result = team_results.get(team) or team_results.get(team.lower())
    if not result:
        return "pending"
    if market == "MONEYLINE":
        won = result.get("won")
        return "pending" if won is None else ("hit" if won else "missed")
    if market == "SPREAD":
        margin = result.get("margin")
        if margin is None or leg.get("line") is None:
            return "pending"
        return "hit" if float(margin) + float(leg["line"]) > 0 else "missed"
    if market == "TOTAL":
        total = result.get("total")
        if total is None or leg.get("line") is None:
            return "pending"
        side = _prediction_side(leg)
        return "hit" if (float(total) < float(leg["line"]) if side == "under" else float(total) > float(leg["line"])) else "missed"
    return "pending"


def _overall_status(results: list[str]) -> str:
    if any(result == "missed" for result in results):
        return "missed"
    if results and all(result == "hit" for result in results):
        return "hit"
    return "pending"


def grade_nfl_parlays(db_file=DB_FILE):
    """Grade pending NFL parlays and persist leg/overall results."""
    initialize_parlay_history(db_file)
    player_stats = get_nfl_final_player_stats()
    team_results = get_nfl_final_team_results()
    if not player_stats and not team_results:
        print("NFL final stats are unavailable; pending NFL parlays were left unchanged.")
        return []

    with get_connection(db_file) as conn:
        rows = conn.execute(
            """
            SELECT * FROM parlay_history
            WHERE UPPER(sport) = 'NFL' AND LOWER(result_status) = 'pending'
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()
        summaries = []
        for row in rows:
            legs = json.loads(row["legs_json"] or "[]")
            results = []
            for leg in legs:
                market = TEAM_MARKET_ALIASES.get(str(leg.get("stat_type") or leg.get("market") or "").upper())
                result = _grade_team_leg(leg, team_results) if market else _grade_player_leg(leg, player_stats)
                leg["result"] = result
                results.append(result)
            status = _overall_status(results)
            notes = row["notes"] or ""
            if status != "pending":
                notes = f"{notes}\nGraded NFL parlay at {utc_now_iso()}.".strip()
            conn.execute(
                "UPDATE parlay_history SET legs_json = ?, result_status = ?, notes = ? WHERE id = ?",
                (json.dumps(legs), status, notes, row["id"]),
            )
            summary = {
                "id": row["id"],
                "difficulty": row["difficulty"],
                "hit": results.count("hit"),
                "missed": results.count("missed"),
                "pending": results.count("pending"),
                "result_status": status,
            }
            summaries.append(summary)
            print(
                f"Parlay #{summary['id']} ({summary['difficulty']}): "
                f"{summary['hit']} hit / {summary['missed']} missed / {summary['pending']} pending -> {summary['result_status']}"
            )
        if not summaries:
            print("No pending NFL parlay history rows found.")
        return summaries
