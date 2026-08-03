"""Plan or capture forward NFL player props with a hard paid-credit ceiling.

``plan`` is offline and read-only. ``capture`` performs free event discovery,
then makes at most one explicitly authorized single-event odds request per
eligible game.  It is intended for the frozen System A shadow workflow only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from nfl_providers import (JsonRawCache, NFL_SPORT_KEY, ODDS_API_BASE,
                           ProviderUnavailable, _fetch_json_structured)

from .game_matching import match_game, parse_dt
from .player_prop_odds import (deduplicate_quotes, normalize_provider_outcomes,
                               pair_quotes, validate_player_prop_rows)
from .player_identity_registry import build_identity_registry
from .snapshots import snapshot_week_dir


LIVE_PLAYER_PROP_MARKETS = (
    "player_receptions", "player_reception_yds", "player_rush_yds",
)
LIVE_REGION = "us"
MAX_CREDITS_PER_EVENT = len(LIVE_PLAYER_PROP_MARKETS)


class LivePaidBudgetExceeded(RuntimeError):
    """A live request would exceed the user's explicit credit authorization."""


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, value: Any) -> None:
    content = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _identity_rows(directory: Path, games: list[dict[str, Any]], *, season: int,
                   week: int) -> tuple[list[dict[str, Any]], str]:
    persisted = _load(directory / "player_identities.json", [])
    if persisted:
        return persisted, "player_identities.json"
    # This is read-only. It lets a free contemporaneous roster capture become
    # usable without invoking the historical paid-prop builder.
    derived = build_identity_registry(directory, games, season=season, week=week)
    return derived, "derived_from_local_identity_evidence"


def plan_capture(*, snapshot_root: Path, season: int, week: int, as_of: str,
                 window_hours: float = 72.0) -> dict[str, Any]:
    """Return a deterministic, network-free capture plan."""
    now = parse_dt(as_of)
    if now is None:
        raise ValueError("--as-of must be an ISO-8601 timestamp")
    if window_hours <= 0:
        raise ValueError("--window-hours must be positive")
    directory = snapshot_week_dir(snapshot_root, "nfl", season, week)
    games = _load(directory / "games.json", [])
    identities, identity_source = _identity_rows(directory, games, season=season, week=week)
    identities_by_game: dict[str, int] = {}
    for row in identities:
        game_id = str(row.get("game_id") or "")
        if game_id and (row.get("canonical_player_id") or row.get("player_id")):
            identities_by_game[game_id] = identities_by_game.get(game_id, 0) + 1

    records = []
    for game in sorted(games, key=lambda item: (str(item.get("kickoff_time")), str(item.get("game_id")))):
        kickoff = parse_dt(game.get("kickoff_time") or game.get("commence_time"))
        hours = (kickoff - now).total_seconds() / 3600 if kickoff else None
        in_window = hours is not None and 0 < hours <= window_hours
        identity_count = identities_by_game.get(str(game.get("game_id")), 0)
        reason = ("INVALID_KICKOFF" if hours is None else "KICKED_OFF" if hours <= 0 else
                  "OUTSIDE_CAPTURE_WINDOW" if not in_window else
                  "IDENTITIES_MISSING" if identity_count == 0 else "READY")
        records.append({
            "game_id": str(game.get("game_id")), "kickoff_time": game.get("kickoff_time"),
            "hours_to_kickoff": round(hours, 6) if hours is not None else None,
            "identity_count": identity_count, "status": reason,
            "maximum_paid_credits": MAX_CREDITS_PER_EVENT if reason == "READY" else 0,
        })
    ready = [record for record in records if record["status"] == "READY"]
    window_games = [record for record in records if record["status"] in {"READY", "IDENTITIES_MISSING"}]
    if ready:
        status = "READY"
    elif any(record["status"] == "IDENTITIES_MISSING" for record in window_games):
        status = "IDENTITIES_MISSING"
    else:
        status = "WAIT_OUTSIDE_CAPTURE_WINDOW"
    return {
        "schema_version": 1, "season": season, "week": week, "as_of": _iso(now),
        "window_hours": window_hours, "status": status, "games": records,
        "games_discovered": len(games), "games_inside_window": len(window_games),
        "games_ready": len(ready), "markets": list(LIVE_PLAYER_PROP_MARKETS),
        "identity_source": identity_source, "identity_records": len(identities),
        "regions": [LIVE_REGION], "maximum_credits_per_event": MAX_CREDITS_PER_EVENT,
        "maximum_paid_credits": len(ready) * MAX_CREDITS_PER_EVENT,
        "network_contacted": False, "paid_credits_used": 0,
        "production_wagering_authorized": False,
    }


def materialize_identities(*, snapshot_root: Path, season: int, week: int) -> dict[str, Any]:
    """Build the canonical registry from local evidence without network access."""
    directory = snapshot_week_dir(snapshot_root, "nfl", season, week)
    games = _load(directory / "games.json", [])
    if not games:
        raise ValueError("games.json is required before materializing player identities")
    rows = build_identity_registry(directory, games, season=season, week=week)
    if not rows:
        return {"schema_version": 1, "season": season, "week": week,
                "status": "IDENTITIES_MISSING", "identity_records": 0,
                "network_contacted": False, "paid_credits_used": 0}
    target = directory / "player_identities.json"
    _write_json(target, rows)
    roster_path = directory / "roster_identities.json"
    return {"schema_version": 1, "season": season, "week": week,
            "status": "IDENTITIES_MATERIALIZED", "identity_records": len(rows),
            "artifact": {"path": target.as_posix(),
                         "sha256": hashlib.sha256(target.read_bytes()).hexdigest()},
            "source_artifacts": ([{"path": roster_path.as_posix(),
                                    "sha256": hashlib.sha256(roster_path.read_bytes()).hexdigest()}]
                                 if roster_path.exists() else []),
            "network_contacted": False, "paid_credits_used": 0}


def validate_authorization(plan: dict[str, Any], *, allow_paid_fetch: bool,
                           max_paid_credits: int | None) -> None:
    required = int(plan["maximum_paid_credits"])
    if required == 0:
        return
    if not allow_paid_fetch:
        raise LivePaidBudgetExceeded("live capture requires --allow-paid-fetch")
    if max_paid_credits is None:
        raise LivePaidBudgetExceeded("--max-paid-credits is required with --allow-paid-fetch")
    if max_paid_credits < 0:
        raise LivePaidBudgetExceeded("--max-paid-credits must be nonnegative")
    if required > max_paid_credits:
        raise LivePaidBudgetExceeded(
            f"maximum live exposure={required} credits exceeds authorized maximum={max_paid_credits}")


def _api_key() -> str:
    value = os.getenv("THE_ODDS_API_KEY") or os.getenv("ODDS_API_KEY")
    if not value:
        raise ProviderUnavailable("THE_ODDS_API_KEY/ODDS_API_KEY is not set")
    return value


def discover_events(api_key: str) -> tuple[list[dict[str, Any]], dict[str, str]]:
    url = f"{ODDS_API_BASE}/sports/{NFL_SPORT_KEY}/events?" + urlencode({"apiKey": api_key})
    response = _fetch_json_structured(url)
    events = response.payload if isinstance(response.payload, list) else []
    return [event for event in events if isinstance(event, dict)], response.headers


def _usage_cost(headers: dict[str, str]) -> int:
    try:
        value = int(headers.get("x-requests-last", MAX_CREDITS_PER_EVENT))
    except (TypeError, ValueError):
        value = MAX_CREDITS_PER_EVENT
    return max(0, value)


def _persist(directory: Path, rows: list[dict[str, Any]], audit: dict[str, Any]) -> None:
    target = directory / "player_prop_odds.json"
    _write_json(target, rows)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest_path = directory / "manifest.json"
    manifest = _load(manifest_path, {"datasets": {}})
    entry = {
        "present": True, "status": "complete" if rows else "optional_empty",
        "records": len(rows), "row_count": len(rows), "sha256": digest,
        "source": "the-odds-api-live", "provider": "the-odds-api",
        "markets": sorted({row["market"] for row in rows}),
        "bookmakers": sorted({row["bookmaker"] for row in rows}),
        "provider_snapshot_timestamps": sorted({row["provider_snapshot_timestamp"] for row in rows}),
    }
    manifest.setdefault("datasets", {})["player_prop_odds"] = entry
    manifest.setdefault("source_versions", {})["player_prop_odds"] = entry["source"]
    manifest.setdefault("source_lineage", {})["player_prop_odds"] = {
        "provider": entry["provider"], "records": len(rows),
        "timestamp_fields": ["requested_snapshot_timestamp", "provider_snapshot_timestamp",
                             "market_last_update", "captured_at", "data_as_of"],
    }
    _write_json(manifest_path, manifest)
    _write_json(directory / "live_player_prop_capture_audit.json", audit)


def capture(*, snapshot_root: Path, cache_root: Path, season: int, week: int,
            as_of: str, window_hours: float = 72.0, allow_paid_fetch: bool = False,
            max_paid_credits: int | None = None,
            _now: datetime | None = None) -> dict[str, Any]:
    """Capture eligible event props without exceeding the authorized ceiling."""
    plan = plan_capture(snapshot_root=snapshot_root, season=season, week=week,
                        as_of=as_of, window_hours=window_hours)
    validate_authorization(plan, allow_paid_fetch=allow_paid_fetch,
                           max_paid_credits=max_paid_credits)
    if not plan["games_ready"]:
        return {**plan, "action": "WAIT", "paid_requests": 0, "rows_persisted": 0}

    observed_now = (_now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    requested_as_of = parse_dt(plan["as_of"])
    assert requested_as_of is not None
    if abs((observed_now - requested_as_of).total_seconds()) > 300:
        raise ValueError("live --as-of must be within five minutes of the current UTC time")

    api_key = _api_key()
    events, discovery_headers = discover_events(api_key)
    directory = snapshot_week_dir(snapshot_root, "nfl", season, week)
    games = _load(directory / "games.json", [])
    players, identity_source = _identity_rows(directory, games, season=season, week=week)
    if identity_source != "player_identities.json":
        _write_json(directory / "player_identities.json", players)
    games_by_id = {str(game.get("game_id")): game for game in games}
    ready_ids = {record["game_id"] for record in plan["games"] if record["status"] == "READY"}
    matched: dict[str, dict[str, Any]] = {}
    discovery_rejections = []
    for event in events:
        diagnostic = match_game(event, games, league="nfl")
        if diagnostic.matched and str(diagnostic.game_id) in ready_ids:
            if str(diagnostic.game_id) in matched:
                raise ValueError(f"multiple live events matched game {diagnostic.game_id}")
            matched[str(diagnostic.game_id)] = event
        elif any(str(event.get(key) or "") for key in ("home_team", "away_team")):
            discovery_rejections.append({"provider_event_id": event.get("id"),
                                         "reasons": diagnostic.reasons})

    cache = JsonRawCache(cache_root)
    credit_cap = int(max_paid_credits or 0)
    credits_used = 0
    paid_requests = 0
    rows: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    request_diagnostics = []
    for game_id in sorted(ready_ids):
        event = matched.get(game_id)
        if event is None:
            rejected.append({"game_id": game_id, "reason": "LIVE_EVENT_NOT_FOUND"})
            continue
        if credits_used + MAX_CREDITS_PER_EVENT > credit_cap:
            raise LivePaidBudgetExceeded(
                f"next request could use {MAX_CREDITS_PER_EVENT} credits; "
                f"used={credits_used}, authorized={credit_cap}")
        event_id = str(event.get("id") or event.get("event_id"))
        params = {"event_id": event_id, "markets": ",".join(LIVE_PLAYER_PROP_MARKETS),
                  "regions": LIVE_REGION, "oddsFormat": "american", "captured_at": plan["as_of"]}
        query = {key: value for key, value in params.items() if key not in {"event_id", "captured_at"}}
        query["apiKey"] = api_key
        url = f"{ODDS_API_BASE}/sports/{NFL_SPORT_KEY}/events/{event_id}/odds?" + urlencode(query)
        response_holder: dict[str, Any] = {}

        def fetch() -> Any:
            nonlocal paid_requests
            paid_requests += 1
            response = _fetch_json_structured(url)
            response_holder["response"] = response
            return response

        payload = cache.get_or_fetch("odds-api", "nfl", season, week,
                                     "live-event-player-props", params, fetch)
        response = response_holder.get("response")
        cost = _usage_cost(response.headers) if response else 0
        credits_used += cost
        request_diagnostics.append({
            "game_id": game_id, "provider_event_id": event_id, "cache_hit": response is None,
            "credits_used": cost, "headers": response.headers if response else {},
        })
        event_payload = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(event_payload, dict):
            rejected.append({"game_id": game_id, "reason": "INVALID_EVENT_PAYLOAD"})
            continue
        normalized, event_rejected = normalize_provider_outcomes(
            event_payload, league="nfl", season=season, week=week, game_id=game_id,
            canonical_players=players, snapshot_timestamp=plan["as_of"],
            requested_snapshot_timestamp=plan["as_of"], captured_at=plan["as_of"],
            source="the-odds-api-live")
        rows.extend(normalized)
        rejected.extend({"game_id": game_id, **item} for item in event_rejected)

    rows, duplicate_diagnostics = deduplicate_quotes(rows)
    complete_keys = {pair["key"] for pair in pair_quotes(rows) if pair["complete"]}
    complete_rows, incomplete_rows = [], []
    for row in rows:
        key = tuple(row.get(field) for field in
                    ("game_id", "canonical_player_id", "market", "bookmaker", "line", "snapshot_timestamp"))
        (complete_rows if key in complete_keys else incomplete_rows).append(row)
    rejected.extend({"game_id": row["game_id"], "reason": "INCOMPLETE_OVER_UNDER_PAIR",
                     "market": row["market"]} for row in incomplete_rows)
    validation_errors = validate_player_prop_rows(complete_rows, games, players)
    if validation_errors:
        raise ValueError("live player-prop persistence validation failed: " + "; ".join(validation_errors))
    complete_rows.sort(key=lambda row: tuple(str(row.get(key, "")) for key in
                       ("game_id", "canonical_player_id", "market", "bookmaker", "line",
                        "selection", "provider_snapshot_timestamp")))
    audit = {
        "schema_version": 1, "season": season, "week": week, "captured_at": plan["as_of"],
        "status": "CAPTURED" if complete_rows else "NO_COMPLETE_QUOTES",
        "network_contacted": True, "free_discovery_requests": 1,
        "paid_requests": paid_requests, "paid_credits_used": credits_used,
        "authorized_paid_credits": credit_cap, "rows_persisted": len(complete_rows),
        "discovery_headers": discovery_headers, "request_diagnostics": request_diagnostics,
        "discovery_rejections": discovery_rejections, "rejections": rejected,
        "duplicate_diagnostics": duplicate_diagnostics,
        "production_wagering_authorized": False,
    }
    # An unavailable/empty live market is diagnostic evidence, not permission
    # to erase a previously captured valid snapshot.
    if complete_rows:
        _persist(directory, complete_rows, audit)
    else:
        _write_json(directory / "live_player_prop_capture_audit.json", audit)
    return audit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "capture"):
        target = subparsers.add_parser(name)
        target.add_argument("--snapshot-root", type=Path, required=True)
        target.add_argument("--season", type=int, required=True)
        target.add_argument("--week", type=int, required=True)
        target.add_argument("--as-of", required=True)
        target.add_argument("--window-hours", type=float, default=72.0)
        target.add_argument("--output", type=Path)
        if name == "capture":
            target.add_argument("--cache-root", type=Path, required=True)
            target.add_argument("--allow-paid-fetch", action="store_true")
            target.add_argument("--max-paid-credits", type=int)
    identities = subparsers.add_parser("identities")
    identities.add_argument("--snapshot-root", type=Path, required=True)
    identities.add_argument("--season", type=int, required=True)
    identities.add_argument("--week", type=int, required=True)
    identities.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    values = {key: value for key, value in vars(args).items()
              if key not in {"command", "output"}}
    if args.command == "plan":
        report = plan_capture(**values)
    elif args.command == "capture":
        report = capture(**values)
    else:
        report = materialize_identities(**values)
    if args.output:
        _write_json(args.output, report)
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
