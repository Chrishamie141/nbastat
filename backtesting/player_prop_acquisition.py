"""Quota-safe, offline planning for event-specific player-prop acquisition."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Iterable
from .markets import CANONICAL_PLAYER_PROP_MARKETS, ODDS_API_PLAYER_PROP_MARKETS, PLAYER_PROP_MARKET_ALIASES


def provider_keys(markets: Iterable[str]) -> tuple[str, ...]:
    wanted=set(markets); bad=wanted-set(CANONICAL_PLAYER_PROP_MARKETS)
    if bad: raise ValueError(f"unsupported player prop markets: {sorted(bad)}")
    reverse={v:k for k,v in PLAYER_PROP_MARKET_ALIASES.items() if k.startswith("player_")}
    return tuple(reverse[m] for m in CANONICAL_PLAYER_PROP_MARKETS if m in wanted)


def plan_acquisition(games: Iterable[dict[str, Any]], cache_root: Path, *, markets: Iterable[str] = CANONICAL_PLAYER_PROP_MARKETS) -> dict[str, Any]:
    """Inspect only deterministic event cache paths; this function has no network path."""
    keys=provider_keys(markets); missing=[]; hits=[]
    for game in games:
        event=str(game.get("provider_event_id") or game.get("odds_event_id") or "")
        path=cache_root / "odds-api" / "nfl" / f"event_{event}_player_props.json"
        (hits if event and path.exists() else missing).append({"game_id":game.get("game_id"), "provider_event_id":event or None, "cache_path":str(path)})
    # Historical event odds cost is provider/account dependent. This is the
    # documented upper planning formula, not a billing assertion.
    return {"network_contacted":False, "games_needing_props":len(missing), "markets_requested":list(keys),
            "requests_required":len(missing), "cache_hits":len(hits),
            "estimated_credits":len(missing)*len(keys)*10, "historical_multiplier":10,
            "event_specific_requests_required":True, "missing":missing, "cached":hits}


def write_cache(path: Path, payload: Any, *, resume: bool=True) -> bool:
    if resume and path.exists(): return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True)+"\n")
    return True
