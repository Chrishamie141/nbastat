"""Deterministic NFL redraft rankings from prior-season production and expert context."""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean, median, pstdev
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
STATS_PATH = BASE_DIR / "data" / "nfl_recent_player_stats.json"
EXPERT_CONTEXT_PATH = BASE_DIR / "data" / "nfl_fantasy_expert_context.json"
SCORING = {"PPR": 1.0, "HALF_PPR": 0.5, "STANDARD": 0.0}
POSITIONS = {"ALL", "QB", "RB", "WR", "TE"}
REPLACEMENT_RANK = {"QB": 12, "RB": 36, "WR": 36, "TE": 12}


def _fantasy_points(game: dict[str, Any], reception_points: float) -> float:
    return (
        float(game.get("PASS_YDS") or 0) * 0.04
        + float(game.get("PASS_TD") or 0) * 4
        - float(game.get("PASS_INT") or 0) * 2
        + float(game.get("RUSH_YDS") or 0) * 0.1
        + float(game.get("RUSH_TD") or 0) * 6
        + float(game.get("REC_YDS") or 0) * 0.1
        + float(game.get("REC_TD") or 0) * 6
        + float(game.get("RECEPTIONS") or 0) * reception_points
    )


def _percentile(values: list[float], value: float) -> float:
    if len(values) < 2:
        return 1.0
    below = sum(candidate < value for candidate in values)
    equal = sum(candidate == value for candidate in values)
    return (below + 0.5 * equal) / len(values)


def _expert_lookup(context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for source in context.get("sources", []):
        for row in source.get("rankings", []):
            item = found.setdefault(row["player"], {"ranks": [], "sources": []})
            item["ranks"].append(int(row["rank"]))
            item["sources"].append(source["name"])
    for item in found.values():
        item["consensus_rank"] = round(mean(item["ranks"]), 1)
    return found


def build_fantasy_rankings(
    scoring: str = "PPR",
    position: str = "ALL",
    limit: int = 25,
    *,
    stats_path: Path = STATS_PATH,
    expert_context_path: Path = EXPERT_CONTEXT_PATH,
) -> dict[str, Any]:
    scoring = str(scoring or "PPR").upper()
    position = str(position or "ALL").upper()
    if scoring not in SCORING:
        raise ValueError("scoring must be PPR, HALF_PPR, or STANDARD")
    if position not in POSITIONS:
        raise ValueError("position must be ALL, QB, RB, WR, or TE")
    limit = max(5, min(int(limit), 100))

    payload = json.loads(stats_path.read_text(encoding="utf-8"))
    context = json.loads(expert_context_path.read_text(encoding="utf-8"))
    experts = _expert_lookup(context)
    candidates = []
    for player, row in payload.get("players", {}).items():
        player_position = str(row.get("position") or "").upper()
        logs = row.get("game_logs") or []
        if player_position not in POSITIONS - {"ALL"} or len(logs) < 2:
            continue
        weekly = [_fantasy_points(game, SCORING[scoring]) for game in logs]
        recent = weekly[-5:]
        ppg = mean(weekly)
        candidates.append({
            "player": player,
            "position": player_position,
            "team": row.get("team") or row.get("last_season_team") or "FA",
            "lastSeasonTeam": row.get("last_season_team") or row.get("team") or "",
            "gamesPlayed": len(logs),
            "lastSeasonFantasyPoints": round(sum(weekly), 1),
            "lastSeasonPointsPerGame": round(ppg, 1),
            "recentPointsPerGame": mean(recent),
            "weekly": weekly,
        })

    position_medians = {}
    for pos in POSITIONS - {"ALL"}:
        values = [row["lastSeasonPointsPerGame"] for row in candidates if row["position"] == pos]
        position_medians[pos] = median(values) if values else 0.0
    by_position: dict[str, list[dict[str, Any]]] = {pos: [] for pos in POSITIONS - {"ALL"}}
    for row in candidates:
        reliability = min(1.0, row["gamesPlayed"] / 14)
        # Prior season is the anchor; recent form and positional regression are bounded inputs.
        projected_ppg = (
            0.72 * row["lastSeasonPointsPerGame"]
            + 0.18 * row["recentPointsPerGame"]
            + 0.10 * position_medians[row["position"]]
        )
        row["projectedPointsPerGame"] = round(projected_ppg, 1)
        row["projectedFantasyPoints"] = round(projected_ppg * 16, 1)
        avg = mean(row["weekly"])
        cv = pstdev(row["weekly"]) / avg if avg > 0 else 2
        row["stability"] = round(max(0, min(100, 100 - cv * 50)))
        row["confidence"] = round(max(35, min(92, 45 + 30 * reliability + 0.17 * row["stability"])))
        by_position[row["position"]].append(row)

    for pos, rows in by_position.items():
        rows.sort(key=lambda item: (-item["projectedFantasyPoints"], item["player"]))
        baseline_index = min(REPLACEMENT_RANK[pos], len(rows)) - 1
        baseline = rows[baseline_index]["projectedFantasyPoints"] if rows else 0
        for rank, row in enumerate(rows, 1):
            row["positionRank"] = rank
            row["valueOverReplacement"] = round(row["projectedFantasyPoints"] - baseline, 1)

    vorps = [row["valueOverReplacement"] for row in candidates]
    for row in candidates:
        expert = experts.get(row["player"])
        expert_score = max(0.0, (31 - expert["consensus_rank"]) / 30) if expert else 0.0
        statistical_score = _percentile(vorps, row["valueOverReplacement"])
        row["rankingScore"] = 0.78 * statistical_score + 0.22 * expert_score
        row["expertConsensusRank"] = expert["consensus_rank"] if expert else None
        row["expertSources"] = expert["sources"] if expert else []

    ranked = sorted(candidates, key=lambda item: (-item["rankingScore"], -item["projectedFantasyPoints"], item["player"]))
    for overall_rank, row in enumerate(ranked, 1):
        row["rank"] = overall_rank
        if overall_rank <= 12:
            row["tier"], row["recommendation"] = "Elite", "Early-round cornerstone"
        elif overall_rank <= 30:
            row["tier"], row["recommendation"] = "Strong starter", "Prioritize at a fair draft cost"
        elif overall_rank <= 60:
            row["tier"], row["recommendation"] = "Value", "Mid-round target"
        else:
            row["tier"], row["recommendation"] = "Depth", "Late-round or watchlist"
        trend = row["recentPointsPerGame"] - row["lastSeasonPointsPerGame"]
        trend_text = "up" if trend > 1 else "down" if trend < -1 else "steady"
        expert_text = (
            f" Current expert signal: #{row['expertConsensusRank']:g}."
            if row["expertConsensusRank"] is not None else " No current top-tier expert signal is attached."
        )
        row["rationale"] = (
            f"{row['lastSeasonPointsPerGame']:.1f} {scoring.replace('_', ' ')} points/game across "
            f"{row['gamesPlayed']} 2025 games; recent form was {trend_text}." + expert_text
        )
        row["recentPointsPerGame"] = round(row["recentPointsPerGame"], 1)
        row.pop("weekly", None)
        row.pop("rankingScore", None)

    visible = [row for row in ranked if position == "ALL" or row["position"] == position][:limit]
    return {
        "items": visible,
        "sourceSeason": payload.get("source_season"),
        "scoring": scoring,
        "position": position,
        "eligiblePlayerCount": len(ranked),
        "methodology": (
            "2025 game-level fantasy production with bounded recent-form regression, positional value over "
            "replacement, and a 22% advisory expert-consensus signal. Rankings are decision support, not guarantees."
        ),
        "researchAsOf": context.get("as_of"),
        "researchSources": [
            {"name": source["name"], "url": source["url"], "updated": source.get("updated")}
            for source in context.get("sources", [])
        ],
    }


def show_fantasy_menu() -> None:
    """Small CLI adapter over the same production ranking service used by the website."""
    report = build_fantasy_rankings()
    print("\n2026 Fantasy Football Draft Board (PPR)")
    for row in report["items"][:20]:
        print(f"{row['rank']:>2}. {row['player']} ({row['position']}{row['positionRank']}, {row['team']}) - {row['tier']}")
