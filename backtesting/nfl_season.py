"""Season-scale NFL snapshot discovery, quota planning, and validation.

Weekly folders remain the persistence boundary.  This module is deliberately an
orchestrator: it never uses an odds provider to discover games and planning is
strictly local/cache-only.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from nfl_providers import JsonRawCache, TEAM_MARKETS

from .snapshots import DATASETS, snapshot_path, validate_snapshot


def _time(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def discover_weeks(root: Path, season: str | int, start_week: int = 1,
                   end_week: int = 18) -> list[int]:
    """Return requested regular-season partitions (including explicit gaps)."""
    return list(range(int(start_week), int(end_week) + 1))


def season_registry(root: Path, season: str | int, weeks: Iterable[int]) -> list[dict[str, Any]]:
    """Read the canonical game universe from immutable weekly snapshots."""
    games: list[dict[str, Any]] = []
    for week in weeks:
        path = snapshot_path(root, "nfl", season, week, "games")
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for row in rows if isinstance(rows, list) else []:
            games.append({key: row.get(key) for key in (
                "league", "season", "week", "game_id", "kickoff_time", "home_team",
                "away_team", "venue", "status", "source")})
    return sorted(games, key=lambda g: (_time(g["kickoff_time"]), str(g["game_id"])))


def group_compatible_odds_requests(games: Iterable[dict[str, Any]], *, hours_before: int = 24,
                                   tolerance_minutes: int = 0) -> list[dict[str, Any]]:
    """Group target timestamps without weakening any game's point-in-time semantics.

    A group's snapshot time is its earliest target, and every member must remain
    within ``tolerance_minutes`` of that target. Execution can therefore only use
    this plan when provider-response reconciliation also proves quote timestamps.
    """
    targets = sorted((((_time(g["kickoff_time"]) - timedelta(hours=hours_before)), g)
                      for g in games), key=lambda item: (item[0], str(item[1].get("game_id"))))
    groups: list[dict[str, Any]] = []
    tolerance = timedelta(minutes=max(0, tolerance_minutes))
    for target, game in targets:
        if groups and target - groups[-1]["_target"] <= tolerance:
            groups[-1]["games"].append(game)
        else:
            groups.append({"_target": target, "timestamp": target.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                           "games": [game]})
    for group in groups:
        group.pop("_target", None)
    return groups


def historical_quote_is_valid(game: dict[str, Any], quote: dict[str, Any], *,
                              hours_before: int = 24, tolerance_minutes: int = 0) -> bool:
    """Prove identity, pre-kickoff timing, and target tolerance for a grouped quote."""
    if str(quote.get("game_id")) != str(game.get("game_id")):
        return False
    captured = quote.get("captured_at") or quote.get("timestamp") or quote.get("snapshot_timestamp")
    if not captured:
        return False
    kickoff = _time(game["kickoff_time"])
    seen = _time(captured)
    target = kickoff - timedelta(hours=hours_before)
    return seen < kickoff and abs(seen - target) <= timedelta(minutes=max(0, tolerance_minutes))


def _load(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def plan_season(root: Path, season: str | int, weeks: Iterable[int], *, hours_before: int = 24,
                tolerance_minutes: int = 0) -> dict[str, Any]:
    """Create an exact cache-aware, non-network season odds plan."""
    cache = JsonRawCache(Path(root).parent / "raw_cache")
    weekly, all_missing = [], []
    for week in weeks:
        datasets = {name: _load(snapshot_path(root, "nfl", season, week, name)) for name in DATASETS}
        game_ids = {str(g.get("game_id")) for g in datasets["games"]}
        odds_ids = {str(o.get("game_id")) for o in datasets["odds"]}
        missing = [g for g in datasets["games"] if str(g.get("game_id")) not in odds_ids]
        hits = 0
        for game in missing:
            target = (_time(game["kickoff_time"]) - timedelta(hours=hours_before)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            params = {"regions": "us", "markets": ",".join(TEAM_MARKETS), "oddsFormat": "american", "date": target}
            hits += int(cache.path("odds-api", "nfl", season, week, "odds", params).exists())
        all_missing.extend(missing)
        paid = len(missing) - hits
        weekly.append({"week": week, "canonical_games": len(game_ids),
            "games_status": "complete" if game_ids else "missing",
            "team_history_status": "complete" if datasets["team_stats"] else "missing",
            "outcomes_status": "complete" if len(datasets["outcomes"]) == len(game_ids) and game_ids else "incomplete",
            "odds_status": "complete" if not missing and game_ids else "missing",
            "historical_requests_needed": len(missing), "cache_hits": hits,
            "paid_requests": paid, "estimated_credits": paid * len(TEAM_MARKETS) * 10})
    optimized = len(group_compatible_odds_requests(all_missing, hours_before=hours_before,
                                                    tolerance_minutes=tolerance_minutes))
    totals = {"season_games": sum(x["canonical_games"] for x in weekly), "season_weeks": len(weekly),
              "historical_http_requests": sum(x["paid_requests"] for x in weekly),
              "cache_hits": sum(x["cache_hits"] for x in weekly), "paid_requests": sum(x["paid_requests"] for x in weekly),
              "estimated_credits": sum(x["estimated_credits"] for x in weekly),
              "naive_request_count": len(all_missing), "optimized_request_count": optimized}
    return {"league": "nfl", "season": int(season), "weeks": weekly, "totals": totals,
            "grouped_execution_enabled": False}


def season_coverage(root: Path, season: str | int, weeks: Iterable[int]) -> dict[str, Any]:
    """Validate partitions independently so one bad week cannot damage earlier ones."""
    records = []
    for week in weeks:
        values = {name: _load(snapshot_path(root, "nfl", season, week, name)) for name in DATASETS}
        games = {str(x.get("game_id")) for x in values["games"]}
        odds = {str(x.get("game_id")) for x in values["odds"]}
        report = validate_snapshot(root, "nfl", str(season), [week])
        records.append({"week": week, "games": len(values["games"]), "odds": len(values["odds"]),
            "outcomes": len(values["outcomes"]), "team_history": len(values["team_stats"]),
            "player_stats": len(values["player_stats"]), "validation_status": "ok" if report.ok else "invalid",
            "games_with_odds": len(games & odds), "games_without_odds": sorted(games - odds),
            "issues": report.errors + report.warnings})
    return {"league": "nfl", "season": int(season),
            "status": "complete" if records and all(r["validation_status"] == "ok" for r in records) else "partial",
            "weeks": records}


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
