"""Strictly offline audit of historical player-prop snapshots and raw caches."""
from __future__ import annotations
import argparse, json
from collections import Counter
from pathlib import Path
from typing import Any
from .markets import CANONICAL_PLAYER_PROP_MARKETS, normalize_player_prop_market
from .player_prop_odds import availability


def _json_files(root: Path):
    return sorted(p for p in root.rglob("*.json") if p.is_file()) if root.exists() else []


def audit_cache(root: Path, *, season: int, start_week: int, end_week: int) -> dict[str, Any]:
    rows=[]; events=set(); books=set(); inspected=[]; invalid=[]
    for path in _json_files(root):
        inspected.append(str(path))
        try: payload=json.loads(path.read_text())
        except (OSError, json.JSONDecodeError): invalid.append(str(path)); continue
        candidates=payload if isinstance(payload,list) else payload.get("data", payload.get("quotes", [])) if isinstance(payload,dict) else []
        # canonical files
        for row in candidates if isinstance(candidates,list) else []:
            market=normalize_player_prop_market(row.get("market") or row.get("key")) if isinstance(row,dict) else None
            if market and row.get("canonical_player_id"):
                copy=dict(row); copy["market"]=market; rows.append(copy)
        # raw provider event payloads
        for event in candidates if isinstance(candidates,list) else []:
            if not isinstance(event,dict) or not event.get("bookmakers"): continue
            for book in event.get("bookmakers",[]):
                for market in book.get("markets",[]):
                    canonical=normalize_player_prop_market(market.get("key"))
                    if canonical:
                        events.add(str(event.get("id"))); books.add(str(book.get("key") or book.get("title")))
                        for outcome in market.get("outcomes",[]): rows.append({"market":canonical,"bookmaker":str(book.get("key") or book.get("title")),"provider_event_id":str(event.get("id")),"week":row_week(path),"canonical_player_id":outcome.get("player_id"),"raw_unreconciled":True})
    filtered=[r for r in rows if start_week <= int(r.get("week") or 0) <= end_week]
    books.update(str(r.get("bookmaker")) for r in filtered if r.get("bookmaker"))
    events.update(str(r.get("provider_event_id")) for r in filtered if r.get("provider_event_id"))
    covered=sorted({int(r.get("week")) for r in filtered if r.get("week")})
    markets=Counter(r["market"] for r in filtered)
    reconciled=[r for r in filtered if r.get("canonical_player_id")]
    avail=availability(reconciled, requested_weeks=range(start_week,end_week+1))
    return {"network_contacted":False,"files_inspected":len(inspected),"inspected_files":inspected,"invalid_files":invalid,
            "existing_prop_rows":len(filtered),"reconciled_rows":len(reconciled),"events":len(events),"bookmakers":sorted(books),
            "games":len({r.get("game_id") for r in filtered if r.get("game_id")}),"players":len({r.get("canonical_player_id") for r in reconciled}),
            "markets":dict(sorted(markets.items())),"weeks_covered":covered,
            "missing_markets":sorted(set(CANONICAL_PLAYER_PROP_MARKETS)-set(markets)),"coverage":avail,
            "cache_state":"usable" if reconciled else "raw_props_require_reconciliation" if filtered else "no_player_props"}


def row_week(path: Path) -> int:
    import re
    match=re.search(r"week[_-]?(\d+)", str(path), re.I)
    return int(match.group(1)) if match else 0


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--snapshot-root",type=Path,required=True); p.add_argument("--season",type=int,required=True); p.add_argument("--start-week",type=int,required=True); p.add_argument("--end-week",type=int,required=True); p.add_argument("--json",action="store_true")
    a=p.parse_args(argv); report=audit_cache(a.snapshot_root,season=a.season,start_week=a.start_week,end_week=a.end_week)
    print(json.dumps(report,indent=2,sort_keys=True)); return 0
if __name__ == "__main__": raise SystemExit(main())
