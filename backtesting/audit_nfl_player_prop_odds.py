"""Strictly offline audit of historical player-prop snapshots and raw caches."""
from __future__ import annotations
import argparse, json
from collections import Counter
from pathlib import Path
from typing import Any
from .markets import CANONICAL_PLAYER_PROP_MARKETS, normalize_player_prop_market
from .player_prop_odds import availability
from .player_identity import normalize_player_id


def _json_files(root: Path):
    return sorted(p for p in root.rglob("*.json") if p.is_file()) if root.exists() else []


def audit_cache(root: Path, *, season: int, start_week: int, end_week: int) -> dict[str, Any]:
    rows=[]; events=set(); books=set(); inspected=[]; invalid=[]; raw_markets=Counter(); raw_by_event={}; funnel={}
    roots=[root]
    raw_root=root.parent/"raw_cache"
    if raw_root.exists() and raw_root.resolve()!=root.resolve(): roots.append(raw_root)
    files=sorted({p for search_root in roots for p in _json_files(search_root)
                  if str(season) in p.parts and (week:=row_week(p)) and start_week<=week<=end_week})
    for path in files:
        inspected.append(str(path))
        try: payload=json.loads(path.read_text())
        except (OSError, json.JSONDecodeError): invalid.append(str(path)); continue
        if path.name == "player_prop_rebuild_audit.json" and isinstance(payload,dict):
            for event_report in (payload.get("event_coverage") or {}).values():
                for market,stages in (event_report.get("funnel") or {}).items():
                    target=funnel.setdefault(market,{key:0 for key in ("raw","normalized","identity_matched","identity_ambiguous","identity_unknown","timestamp_eligible","deduplicated","paired","persisted","rejected")})
                    target.setdefault("rejections",{})
                    for key in tuple(target):
                        if key != "rejections": target[key]+=int(stages.get(key,stages.get(key+"_rows",0)))
                    for reason,count in stages.get("rejections",{}).items(): target["rejections"][reason]=target["rejections"].get(reason,0)+count
            continue
        candidates=payload if isinstance(payload,list) else payload.get("data", payload.get("quotes", [])) if isinstance(payload,dict) else []
        # canonical files
        for row in candidates if isinstance(candidates,list) else []:
            market=normalize_player_prop_market(row.get("market") or row.get("key")) if isinstance(row,dict) else None
            # Persisted rows without an ID must remain visible to identity
            # diagnostics; they are not silently erased or counted as one null.
            if market and (row.get("game_id") or row.get("provider_snapshot_timestamp") or row.get("snapshot_timestamp")):
                copy=dict(row); copy["market"]=market; rows.append(copy)
        # Raw event endpoints return data as an object; list endpoints return a
        # list.  Treat both shapes identically (the old audit silently skipped
        # the former).
        event_candidates=candidates if isinstance(candidates,list) else [candidates] if isinstance(candidates,dict) else []
        for event in event_candidates:
            if not isinstance(event,dict) or not event.get("bookmakers"): continue
            for book in event.get("bookmakers",[]):
                for market in book.get("markets",[]):
                    canonical=normalize_player_prop_market(market.get("key"))
                    if canonical:
                        events.add(str(event.get("id"))); books.add(str(book.get("key") or book.get("title")))
                        count=len(market.get("outcomes",[]) or []); raw_markets[market.get("key")]+=count
                        raw_by_event.setdefault(str(event.get("id")),Counter())[market.get("key")]+=count
    filtered=[r for r in rows if start_week <= int(r.get("week") or 0) <= end_week]
    books.update(str(r.get("bookmaker")) for r in filtered if r.get("bookmaker"))
    events.update(str(r.get("provider_event_id")) for r in filtered if r.get("provider_event_id"))
    covered=sorted({int(r.get("week")) for r in filtered if r.get("week")})
    markets=Counter(r["market"] for r in filtered)
    reconciled=[r for r in filtered if normalize_player_id(r.get("canonical_player_id")) is not None]
    avail=availability(reconciled, requested_weeks=range(start_week,end_week+1))
    from .player_prop_odds import pair_quotes
    complete=sum(p["complete"] for p in pair_quotes(reconciled))
    unmatched=sum(r.get("reconciliation_status") in {"unknown_player","unmatched"} for r in filtered)
    ambiguous=sum(r.get("reconciliation_status") == "ambiguous_player" for r in filtered)
    invalid_ts=sum(not (r.get("provider_snapshot_timestamp") or r.get("snapshot_timestamp")) for r in filtered)
    line_ready="READY" if reconciled else "NOT_READY"; price_ready="READY" if complete else "NOT_READY"
    canonical_ids={normalize_player_id(r.get("canonical_player_id")) for r in reconciled}
    provider_names={str(r.get("provider_player_name")) for r in filtered if r.get("provider_player_name") not in (None,"")}
    missing_ids=sum(normalize_player_id(r.get("canonical_player_id")) is None for r in filtered)
    literal_none_ids=sum(str(r.get("canonical_player_id")).strip().casefold() in {"none","null"} for r in filtered if r.get("canonical_player_id") is not None)
    fallback={str(r.get("provider_player_name")).strip().casefold() for r in filtered if normalize_player_id(r.get("canonical_player_id")) is None and r.get("provider_player_name")}
    identities_by_id={}
    for r in reconciled:
        if normalize_player_id(r.get("canonical_player_id")):
            name=" ".join("".join(c for c in str(r.get("provider_player_name") or r.get("player_name") or "").casefold() if c.isalnum() or c.isspace()).split())
            identities_by_id.setdefault(str(r["canonical_player_id"]),set()).add((name,str(r.get("team") or "").casefold()))
    identity_collisions={key:sorted([list(v) for v in values]) for key,values in identities_by_id.items() if len(values)>1}
    collisions=len(identity_collisions)
    match_methods=Counter(str(r.get("reconciliation_method") or r.get("reconciliation_status") or "UNKNOWN") for r in reconciled)
    funnel_ambiguous=sum(int(stages.get("identity_ambiguous",0)) for stages in funnel.values())
    funnel_unknown=sum(int(stages.get("identity_unknown",0)) for stages in funnel.values())
    invariant_errors=[]
    if sum(markets.values()) != len(filtered): invariant_errors.append("coverage_by_market_sum_must_equal_quote_count")
    return {"network_contacted":False,"files_inspected":len(inspected),"inspected_files":inspected,"invalid_files":invalid,
            "existing_prop_rows":len(filtered),"reconciled_rows":len(reconciled),"events":len(events),"bookmakers":sorted(books),
            "games":len({r.get("game_id") for r in filtered if r.get("game_id")}),"players":len(canonical_ids | {"fallback:"+n for n in fallback}),
            "unique_canonical_player_ids":len(canonical_ids),"unique_provider_player_names":len(provider_names),
            "quotes_missing_canonical_player_id":missing_ids,"accepted_quotes_with_null_id":missing_ids-literal_none_ids,
            "accepted_quotes_with_literal_none_id":literal_none_ids,
            "quotes_using_fallback_identity":sum(1 for r in filtered if normalize_player_id(r.get("canonical_player_id")) is None and r.get("provider_player_name")),
            "identity_collision_count":collisions,"identity_collisions":identity_collisions,
            "identity_match_method_counts":dict(sorted(match_methods.items())),
            "fallback_ids_used":sum(1 for value in canonical_ids if value.startswith("history:")),
            "provider_id_matches":match_methods.get("EXACT_PROVIDER_ID",0),
            "name_team_game_matches":match_methods.get("EXACT_NAME_TEAM_GAME",0),
            "markets":dict(sorted(markets.items())),"weeks_covered":covered,
            "missing_markets":sorted(set(CANONICAL_PLAYER_PROP_MARKETS)-set(markets)),"coverage":avail,
            "quote_count":len(filtered),"paired_over_under_count":complete,"gradeable_quote_count":complete*2,
            "unmatched_player_count":unmatched+funnel_unknown,"ambiguous_player_count":ambiguous+funnel_ambiguous,"invalid_timestamp_count":invalid_ts,
            "coverage_by_market":dict(sorted(markets.items())),"coverage_by_week":dict(sorted(Counter(int(r.get("week") or 0) for r in filtered).items())),
            "historical_line_readiness":line_ready,"historical_price_readiness":price_ready,
            "PLAYER_IDENTITY_READY":"READY" if reconciled and not collisions and not missing_ids else "PARTIAL" if reconciled and not collisions else "NOT_READY",
            "PLAYER_PROP_LINE_READY":line_ready,"PLAYER_PROP_PRICE_READY":price_ready,"PLAYER_PROP_GRADING_READY":"READY",
            "MODEL_SGP_READY":"READY","HISTORICAL_SGP_BOOK_PRICE_READY":"NOT_READY",
            "raw_provider_coverage":dict(sorted(raw_markets.items())),
            "raw_provider_coverage_by_event":{event:dict(sorted(counts.items())) for event,counts in sorted(raw_by_event.items())},
            "market_funnel":dict(sorted(funnel.items())),
            "invariant_errors":invariant_errors,"invariants_passed":not invariant_errors,
            "cache_state":"usable" if reconciled else "raw_props_require_reconciliation" if raw_markets else "no_player_props"}


def row_week(path: Path) -> int:
    import re
    match=re.search(r"week[_-]?(\d+)", str(path), re.I)
    return int(match.group(1)) if match else 0


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--snapshot-root",type=Path,default=Path("backtesting/data/snapshots")); p.add_argument("--season",type=int,required=True); p.add_argument("--start-week",type=int,required=True); p.add_argument("--end-week",type=int,required=True); p.add_argument("--json",action="store_true")
    a=p.parse_args(argv); report=audit_cache(a.snapshot_root,season=a.season,start_week=a.start_week,end_week=a.end_week)
    print(json.dumps(report,indent=2,sort_keys=True)); return 0
if __name__ == "__main__": raise SystemExit(main())
