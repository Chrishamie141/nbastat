"""Explicit, single-event The Odds API historical player-market check."""
from __future__ import annotations
import argparse, json, os
from urllib.parse import urlencode
from nfl_providers import NFL_SPORT_KEY, ODDS_API_BASE, _fetch_json, _redact_url
from .markets import ODDS_API_PLAYER_PROP_MARKETS


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--event-id",required=True); p.add_argument("--date",required=True,help="ISO-8601 historical snapshot")
    p.add_argument("--markets",default=",".join(ODDS_API_PLAYER_PROP_MARKETS)); p.add_argument("--acknowledge-quota",action="store_true")
    a=p.parse_args(argv); selected=tuple(x.strip() for x in a.markets.split(",") if x.strip())
    unknown=set(selected)-set(ODDS_API_PLAYER_PROP_MARKETS)
    if unknown: p.error(f"unsupported market(s): {sorted(unknown)}")
    estimated=len(selected)*10
    print(f"Provider: The Odds API; endpoint: historical event odds; markets={','.join(selected)}")
    print(f"WARNING: this request may consume quota; estimated cost units={estimated} (verify account billing). No season download is performed.")
    if not a.acknowledge_quota:
        print("No network request made. Re-run with --acknowledge-quota after reviewing the estimate."); return 2
    key=os.getenv("THE_ODDS_API_KEY") or os.getenv("ODDS_API_KEY")
    if not key: print("No network request made: THE_ODDS_API_KEY/ODDS_API_KEY is not set."); return 2
    params={"apiKey":key,"regions":"us","markets":",".join(selected),"oddsFormat":"american","date":a.date}
    url=f"{ODDS_API_BASE}/historical/sports/{NFL_SPORT_KEY}/events/{a.event_id}/odds?{urlencode(params)}"
    print(f"Requesting exactly one event: {_redact_url(url)}")
    data=_fetch_json(url); payload=data.get("data",data) if isinstance(data,dict) else data
    markets=sorted({m.get("key") for b in (payload or {}).get("bookmakers",[]) for m in b.get("markets",[])})
    books=sorted({b.get("key") for b in (payload or {}).get("bookmakers",[])})
    print(json.dumps({"historical_availability":"verified","markets":markets,"bookmakers":books,"provider_timestamp":data.get("timestamp") if isinstance(data,dict) else None},indent=2)); return 0
if __name__ == "__main__": raise SystemExit(main())
