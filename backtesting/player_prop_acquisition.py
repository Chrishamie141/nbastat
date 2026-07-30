"""Deterministic, secret-free planning and caching for historical player props."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from nfl_providers import JsonRawCache
from .markets import CANONICAL_PLAYER_PROP_MARKETS, PLAYER_PROP_MARKET_ALIASES
from .team_history import prediction_cutoff

HISTORICAL_CREDIT_MULTIPLIER = 10
REGIONS = ("us",)
ODDS_FORMAT = "american"


def provider_keys(markets: Iterable[str]) -> tuple[str, ...]:
    wanted = set(markets)
    bad = wanted - set(CANONICAL_PLAYER_PROP_MARKETS)
    if bad:
        raise ValueError(f"unsupported player prop markets: {sorted(bad)}")
    reverse = {v: k for k, v in PLAYER_PROP_MARKET_ALIASES.items() if k.startswith("player_")}
    return tuple(reverse[m] for m in CANONICAL_PLAYER_PROP_MARKETS if m in wanted)


def request_params(event_id: str, requested_at: str, keys: Iterable[str]) -> dict[str, str]:
    return {"event_id": str(event_id), "date": requested_at, "regions": ",".join(REGIONS),
            "markets": ",".join(keys), "oddsFormat": ODDS_FORMAT}


def cache_path(cache_root: Path, season: int | str, week: int, event_id: str,
               requested_at: str, keys: Iterable[str]) -> Path:
    cache = JsonRawCache(cache_root)
    return cache.path("odds-api", "nfl", season, week, "event-player-props",
                      request_params(event_id, requested_at, keys))


def inspect_cache(path: Path, *, event_id: str, requested_at: str,
                  keys: Iterable[str]) -> tuple[str, dict[str, Any] | None, list[str]]:
    """Validate raw response structure/identity without changing the cache."""
    if not path.exists():
        return "missing", None, []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return "invalid", None, [f"malformed_json: {exc}"]
    data = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(data, list):
        matches=[item for item in data if isinstance(item,dict) and str(item.get("id") or item.get("event_id")) == str(event_id)]
        event=matches[0] if len(matches)==1 else None
    else: event=data
    errors: list[str] = []
    if not isinstance(event, dict):
        errors.append("response must contain one matching event object")
    else:
        if str(event.get("id") or event.get("event_id")) != str(event_id): errors.append("unmatched_event")
        if isinstance(payload,dict) and "data" in payload and not isinstance(payload.get("timestamp"), str): errors.append("missing_provider_snapshot_timestamp")
        returned = {m.get("key") for b in event.get("bookmakers", []) if isinstance(b, dict)
                    for m in b.get("markets", []) if isinstance(m, dict)}
        unexpected = returned - set(keys)
        if unexpected: errors.append(f"unexpected_markets={sorted(unexpected)}")
    return ("invalid", None, errors) if errors else ("valid", payload, [])


def plan_acquisition(games: Iterable[dict[str, Any]], cache_root: Path, *,
                     markets: Iterable[str] = CANONICAL_PLAYER_PROP_MARKETS,
                     season: int | str | None = None) -> dict[str, Any]:
    """Return an exact offline plan. One multi-market HTTP request is one request.

    The Odds API historical charge is ``10 * regions * markets`` credits, while
    the HTTP request count remains one per event snapshot.
    """
    keys = provider_keys(markets); records=[]
    for game in games:
        event = str(game.get("provider_event_id") or game.get("the_odds_api_event_id") or game.get("odds_event_id") or "")
        cutoff = prediction_cutoff(game)
        requested = cutoff.isoformat().replace("+00:00", "Z") if cutoff else None
        week = int(game.get("week") or 0); use_season = season or game.get("season") or "unknown"
        path = cache_path(cache_root, use_season, week, event, requested or "invalid", keys)
        state, _payload, errors = inspect_cache(path,event_id=event,requested_at=requested or "",keys=keys) if event and requested else ("invalid",None,["missing_event_or_cutoff"])
        # Missing event identity blocks acquisition. A missing cutoff remains a
        # visible paid-plan item (rather than concealing possible work), though
        # execution validation will refuse its invalid historical date.
        actionable=bool(event)
        records.append({"game_id":game.get("game_id"),"provider_event_id":event or None,"season":int(use_season) if str(use_season).isdigit() else use_season,
            "week":week,"kickoff":game.get("kickoff_time"),"prediction_cutoff":requested,"requested_snapshot_timestamp":requested,
            "markets":list(keys),"cache_path":str(path),"cache_state":state,"cache_errors":errors,
            "raw_cache_hit":state != "missing","validated_cache_hit":state == "valid",
            "requires_paid_fetch":actionable and state in {"missing","invalid"},"request_blocked":not actionable})
    paid=sum(r["requires_paid_fetch"] for r in records)
    credits_per_request=len(keys)*len(REGIONS)*HISTORICAL_CREDIT_MULTIPLIER
    coverage={key:{"games_cached":sum(r["validated_cache_hit"] for r in records),
                   "games_requested":len(records),"paid_requests_required":paid} for key in keys}
    hits=sum(r["raw_cache_hit"] for r in records); valid=sum(r["validated_cache_hit"] for r in records)
    return {"network_contacted":False,"games":len(records),"games_needing_props":paid,
        "markets_requested":list(keys),"provider_requests":len(records),"requests_required":paid,
        "raw_cache_hits":hits,"cache_hits":hits,"validated_cache_hits":valid,
        "invalid_cache_entries":sum(r["cache_state"] == "invalid" for r in records),
        "paid_requests_required":paid,"credits_per_paid_request":credits_per_request,
        "estimated_credits":paid*credits_per_request,"total_paid_request_budget":paid,
        "total_paid_credit_budget":paid*credits_per_request,"request_cost_semantics":
        f"10 credits x {len(REGIONS)} region x {len(keys)} markets = {credits_per_request} credits per event request",
        "event_specific_requests_required":True,"per_game_requests":records,"per_market_coverage":coverage,
        "missing":[r for r in records if r["requires_paid_fetch"]],"cached":[r for r in records if r["validated_cache_hit"]]}


def write_cache(path: Path, payload: Any, *, resume: bool = True) -> bool:
    if resume and path.exists(): return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8"); tmp.replace(path)
    return True
