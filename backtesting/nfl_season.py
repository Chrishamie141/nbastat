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

from nfl_providers import (JsonRawCache, TEAM_MARKETS, TheOddsApiNflProvider,
                           normalize_odds_events)

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
                              hours_before: int = 24, tolerance_minutes: int = 5) -> bool:
    """Return whether a quote was knowable at the configured prediction time.

    ``snapshot_timestamp`` is The Odds API's historical snapshot time and is
    therefore the quote's ``captured_at``/``data_as_of``. ``market_last_update``
    is separate bookmaker provenance and may be older, but never newer, than
    the snapshot.  The provider returns the closest snapshot at or before the
    requested date, so tolerance is intentionally one-sided: a later snapshot
    would leak information, while an earlier snapshot within the configured
    provider-resolution window is safe.
    """
    if str(quote.get("game_id")) != str(game.get("game_id")):
        return False
    snapshot = quote.get("snapshot_timestamp")
    if not snapshot:
        return False
    try:
        kickoff = _time(game["kickoff_time"])
        snap = _time(snapshot)
    except (TypeError, ValueError):
        return False
    target = kickoff - timedelta(hours=hours_before)
    market_update = quote.get("market_last_update")
    required = (quote.get("market"), quote.get("selection"), quote.get("sportsbook"),
                quote.get("odds"), quote.get("line"))
    if any(value is None or value == "" for value in required):
        return False
    return (snap < kickoff and snap <= target and
            target - snap <= timedelta(minutes=max(0, tolerance_minutes)) and
            (not market_update or _safe_time_at_or_before(market_update, snap)))


def _safe_time_at_or_before(value: Any, bound: datetime) -> bool:
    try:
        return _time(value) <= bound
    except (TypeError, ValueError):
        return False


def _cached_rows(cache: JsonRawCache, season: str | int, week: int,
                 games: list[dict[str, Any]], timestamp: str) -> tuple[bool, list[dict[str, Any]], dict[str, Any]]:
    """Normalize a raw cache entry without constructing a network request."""
    path = cache.path("odds-api", "nfl", season, week, "odds", _request_params(timestamp))
    if not path.exists():
        return False, [], {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True, [], {"cache_error": "malformed_json"}
    events = payload.get("data", []) if isinstance(payload, dict) else payload
    response_timestamp = payload.get("timestamp") if isinstance(payload, dict) else timestamp
    diagnostics: dict[str, Any] = {}
    rows = normalize_odds_events(events, games, diagnostics=diagnostics)
    for row in rows:
        row.update({"snapshot_timestamp": response_timestamp,
                    "captured_at": response_timestamp, "data_as_of": response_timestamp})
    diagnostics.update({"requested_date": timestamp,
                        "response_timestamp": response_timestamp,
                        "cache_path": str(path)})
    return True, rows, diagnostics


def audit_cached_odds(root: Path, season: str | int, weeks: Iterable[int], *,
                      hours_before: int = 24, tolerance_minutes: int = 5) -> dict[str, Any]:
    """Describe historical timestamp relationships using cache files only."""
    games = season_registry(root, season, weeks)
    cache = JsonRawCache(Path(root).parent / "raw_cache")
    requests, matches = [], []
    for group in group_compatible_odds_requests(games, hours_before=hours_before,
                                                 tolerance_minutes=tolerance_minutes):
        week = min(int(g["week"]) for g in group["games"])
        raw, rows, detail = _cached_rows(cache, season, week, group["games"], group["timestamp"])
        requests.append({"requested_date": group["timestamp"], "raw_cache_hit": raw,
                         "response_timestamp": detail.get("response_timestamp"),
                         "provider_event_commence_times": sorted({r.get("commence_time") for r in rows if r.get("commence_time")})})
        for game in group["games"]:
            event_rows = [r for r in rows if str(r.get("game_id")) == str(game["game_id"])]
            if not event_rows:
                continue
            snapshot = event_rows[0].get("snapshot_timestamp")
            target = _time(game["kickoff_time"]) - timedelta(hours=hours_before)
            updates = sorted({r.get("market_last_update") for r in event_rows if r.get("market_last_update")})
            matches.append({"game_id": str(game["game_id"]), "kickoff": game["kickoff_time"],
                "target_time": target.isoformat().replace("+00:00", "Z"),
                "requested_date": group["timestamp"], "snapshot_timestamp": snapshot,
                "market_last_updates": updates,
                "captured_at": event_rows[0].get("captured_at"),
                "data_as_of": event_rows[0].get("data_as_of"),
                "snapshot_minus_target_seconds": (_time(snapshot) - target).total_seconds() if snapshot else None,
                "market_update_minus_snapshot_seconds": [(_time(v) - _time(snapshot)).total_seconds() for v in updates] if snapshot else [],
                "market_update_minus_target_seconds": [(_time(v) - target).total_seconds() for v in updates],
                "kickoff_minus_snapshot_seconds": (_time(game["kickoff_time"]) - _time(snapshot)).total_seconds() if snapshot else None,
                "valid": any(historical_quote_is_valid(game, r, hours_before=hours_before,
                             tolerance_minutes=tolerance_minutes) for r in event_rows)})
    return {"requests": requests, "matched_canonical_events": matches}


def _request_params(timestamp: str) -> dict[str, Any]:
    return {"regions": "us", "markets": ",".join(TEAM_MARKETS),
            "oddsFormat": "american", "date": timestamp}


def execute_grouped_odds(root: Path, season: str | int, weeks: Iterable[int], *,
                         hours_before: int = 24, tolerance_minutes: int = 5,
                         provider: TheOddsApiNflProvider | None = None) -> dict[str, Any]:
    """Fetch grouped snapshots, safely reconcile them, then individually retry gaps."""
    week_set = set(weeks)
    games = [g for g in season_registry(root, season, week_set)
             if not any(historical_quote_is_valid(g, row, hours_before=hours_before,
                                                   tolerance_minutes=tolerance_minutes)
                        for row in _load(snapshot_path(root, "nfl", season,
                                                       int(g["week"]), "odds")))]
    cache = JsonRawCache(Path(root).parent / "raw_cache")
    provider = provider or TheOddsApiNflProvider(cache=cache)
    diagnostics: dict[str, Any] = {"canonical_games_requested": len(games),
        "provider_events_returned": 0, "matched_events": 0, "unmatched_events": 0,
        "ambiguous_events": 0, "invalid_timestamp_events": 0,
        "grouped_requests_executed": 0, "grouped_cache_hits": 0,
        "grouped_paid_requests": 0, "grouped_games_satisfied": [],
        "fallback_games_attempted": [], "fallback_cache_hits": 0,
        "fallback_paid_requests": 0, "fallback_games_satisfied": [],
        "games_successfully_satisfied": [], "games_requiring_fallback": []}
    accepted: dict[str, list[dict[str, Any]]] = {}
    groups = group_compatible_odds_requests(games, hours_before=hours_before,
                                             tolerance_minutes=tolerance_minutes)
    for group in groups:
        diagnostics["grouped_requests_executed"] += 1
        rows = provider.fetch_odds(season, min(int(g["week"]) for g in group["games"]),
                                   group["games"], snapshot_time=group["timestamp"])
        pd = getattr(provider, "last_diagnostics", {})
        if pd.get("raw_cache_hit"):
            diagnostics["grouped_cache_hits"] += 1
        else:
            diagnostics["grouped_paid_requests"] += 1
        diagnostics["provider_events_returned"] += pd.get("provider_events_received", 0)
        diagnostics["matched_events"] += pd.get("provider_events_matched", 0)
        ambiguous = pd.get("provider_events_ambiguous", 0)
        diagnostics["ambiguous_events"] += ambiguous
        diagnostics["unmatched_events"] += pd.get("provider_events_discarded", 0) - ambiguous
        for game in group["games"]:
            valid = [r for r in rows if historical_quote_is_valid(
                game, r, hours_before=hours_before, tolerance_minutes=tolerance_minutes)]
            invalid = [r for r in rows if str(r.get("game_id")) == str(game["game_id"]) and r not in valid]
            diagnostics["invalid_timestamp_events"] += len({r.get("event_id") for r in invalid})
            if valid:
                accepted[str(game["game_id"])] = valid
                diagnostics["grouped_games_satisfied"].append(str(game["game_id"]))
            else:
                diagnostics["games_requiring_fallback"].append(str(game["game_id"]))
    # A fallback is explicit and uses the original per-game timestamp/cache identity.
    for game in games:
        gid = str(game["game_id"])
        if gid in accepted:
            continue
        diagnostics["fallback_games_attempted"].append(gid)
        target = (_time(game["kickoff_time"]) - timedelta(hours=hours_before)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        rows = provider.fetch_odds(season, int(game["week"]), [game], snapshot_time=target)
        fallback_diag = getattr(provider, "last_diagnostics", {})
        if fallback_diag.get("raw_cache_hit"):
            diagnostics["fallback_cache_hits"] += 1
        else:
            diagnostics["fallback_paid_requests"] += 1
        valid = [r for r in rows if historical_quote_is_valid(game, r, hours_before=hours_before,
                                                                tolerance_minutes=tolerance_minutes)]
        if valid:
            accepted[gid] = valid
            diagnostics["fallback_games_satisfied"].append(gid)
    for week in sorted(week_set):
        path = snapshot_path(root, "nfl", season, week, "odds")
        prior = _load(path)
        additions = [row for game in games if int(game["week"]) == week
                     for row in accepted.get(str(game["game_id"]), [])]
        if additions:
            identity = lambda r: tuple(r.get(k) for k in ("game_id", "snapshot_timestamp", "captured_at", "bookmaker", "market", "selection", "line", "odds"))
            write_json_atomic(path, list({identity(r): r for r in prior + additions}.values()))
    diagnostics["games_successfully_satisfied"] = sorted(accepted)
    diagnostics["grouped_games_satisfied"] = sorted(set(diagnostics["grouped_games_satisfied"]))
    diagnostics["fallback_games_satisfied"] = sorted(set(diagnostics["fallback_games_satisfied"]))
    diagnostics["games_requiring_fallback"] = sorted(set(diagnostics["games_requiring_fallback"]))
    diagnostics["games_incomplete"] = sorted(str(g["game_id"]) for g in games if str(g["game_id"]) not in accepted)
    return diagnostics


def _load(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def plan_season(root: Path, season: str | int, weeks: Iterable[int], *, hours_before: int = 24,
                tolerance_minutes: int = 5) -> dict[str, Any]:
    """Create a cache-aware plan that proves cached payload usability offline."""
    cache = JsonRawCache(Path(root).parent / "raw_cache")
    weekly: list[dict[str, Any]] = []
    all_missing: list[dict[str, Any]] = []
    for week in weeks:
        datasets = {name: _load(snapshot_path(root, "nfl", season, week, name)) for name in DATASETS}
        game_ids = {str(g.get("game_id")) for g in datasets["games"]}
        usable_odds_ids = {str(game["game_id"]) for game in datasets["games"]
            if any(historical_quote_is_valid(game, quote, hours_before=hours_before,
                                             tolerance_minutes=tolerance_minutes)
                   for quote in datasets["odds"])}
        missing = [g for g in datasets["games"] if str(g.get("game_id")) not in usable_odds_ids]
        all_missing.extend(missing)
        weekly.append({"week": int(week), "canonical_games": len(game_ids),
            "games_status": "complete" if game_ids else "missing",
            "team_history_status": "complete" if datasets["team_stats"] else "missing",
            "outcomes_status": "complete" if len(datasets["outcomes"]) == len(game_ids) and game_ids else "incomplete",
            "odds_status": "complete" if not missing and game_ids else "missing",
            "historical_requests_needed": len(missing), "naive_request_count": len(missing),
            "games_with_usable_cached_odds": 0, "raw_cache_hits": 0,
            "validated_cache_hits": 0, "invalid_cache_hits": 0})

    groups = group_compatible_odds_requests(all_missing, hours_before=hours_before,
                                             tolerance_minutes=tolerance_minutes)
    physical_raw_hits = validated_groups = 0
    for group in groups:
        owner = min(int(g["week"]) for g in group["games"])
        raw_hit, rows, _ = _cached_rows(cache, season, owner, group["games"], group["timestamp"])
        satisfied = {str(g["game_id"]) for g in group["games"]
            if any(historical_quote_is_valid(g, row, hours_before=hours_before,
                                             tolerance_minutes=tolerance_minutes)
                   for row in rows)}
        physical_raw_hits += int(raw_hit)
        validated_groups += int(raw_hit and len(satisfied) == len(group["games"]))
        week_row = next(row for row in weekly if row["week"] == owner)
        week_row["planned_grouped_requests"] = week_row.get("planned_grouped_requests", 0) + 1
        if raw_hit:
            game_count = len(group["games"])
            week_row["raw_cache_hits"] += game_count
            week_row["validated_cache_hits"] += len(satisfied)
            week_row["invalid_cache_hits"] += game_count - len(satisfied)
            week_row["games_with_usable_cached_odds"] += len(satisfied)

    for row in weekly:
        row.setdefault("planned_grouped_requests", 0)
        # A fully validated grouped cache entry requires no paid replacement.
        owned = [g for g in groups if min(int(x["week"]) for x in g["games"]) == row["week"]]
        paid = 0
        for group in owned:
            raw, rows, _ = _cached_rows(cache, season, row["week"], group["games"], group["timestamp"])
            if not raw or not all(any(historical_quote_is_valid(game, quote,
                    hours_before=hours_before, tolerance_minutes=tolerance_minutes) for quote in rows)
                    for game in group["games"]):
                paid += 1
        row.update({"individual_fallback_requests": 0, "cache_hits": row["validated_cache_hits"],
                    "paid_requests": paid,
                    "estimated_credits": paid * len(TEAM_MARKETS) * 10})

    totals = {"season_games": sum(x["canonical_games"] for x in weekly),
              "season_weeks": len(weekly), "naive_request_count": len(all_missing),
              "planned_grouped_requests": len(groups), "optimized_request_count": len(groups),
              "individual_fallback_requests": 0,
              "raw_cache_hits": sum(x["raw_cache_hits"] for x in weekly),
              "validated_cache_hits": sum(x["validated_cache_hits"] for x in weekly),
              "invalid_cache_hits": sum(x["invalid_cache_hits"] for x in weekly),
              "games_with_usable_cached_odds": sum(x["games_with_usable_cached_odds"] for x in weekly),
              "grouped_raw_cache_hits": physical_raw_hits,
              "grouped_validated_cache_hits": validated_groups,
              "cache_hits": sum(x["validated_cache_hits"] for x in weekly),
              "paid_requests": sum(x["paid_requests"] for x in weekly),
              "historical_http_requests": sum(x["paid_requests"] for x in weekly)}
    totals["estimated_credits"] = totals["paid_requests"] * len(TEAM_MARKETS) * 10
    return {"league": "nfl", "season": int(season), "weeks": weekly, "totals": totals,
            "grouped_execution_enabled": True,
            "fallback_note": "Only identity-, timestamp-, and market-valid normalized cache rows satisfy a game."}


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
