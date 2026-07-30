"""Plan or execute quota-safe historical NFL player-prop ingestion."""
from __future__ import annotations

import argparse, hashlib, json, os
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from nfl_providers import (JsonRawCache, NFL_SPORT_KEY, ODDS_API_BASE, ProviderUnavailable,
                           _fetch_json, _redact_url)
from .config import SNAPSHOTS_DIR
from .player_prop_acquisition import inspect_cache, plan_acquisition, request_params
from .player_prop_odds import (deduplicate_quotes, normalize_provider_outcomes, pair_quotes,
                               validate_player_prop_rows)
from .snapshots import snapshot_week_dir


class PaidBudgetExceeded(RuntimeError): pass


def _load(path: Path, default: Any) -> Any:
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): return default


def load_registry(root: Path, season: int, start: int, end: int) -> list[dict[str, Any]]:
    result=[]
    for week in range(start,end+1):
        directory=snapshot_week_dir(root,"nfl",season,week)
        odds=_load(directory/"odds.json",[])
        by_game={}
        for row in odds:
            event=row.get("provider_event_id") or row.get("event_id")
            if event: by_game.setdefault(str(row.get("game_id")),set()).add(str(event))
        for game in _load(directory/"games.json",[]):
            copy=dict(game); ids=by_game.get(str(game.get("game_id")),set())
            explicit=copy.get("provider_event_id") or copy.get("the_odds_api_event_id") or copy.get("odds_event_id")
            if explicit: ids.add(str(explicit))
            if len(ids)==1: copy["provider_event_id"]=next(iter(ids))
            copy.setdefault("week",week); copy.setdefault("season",season); result.append(copy)
    return sorted(result,key=lambda g:(int(g["week"]),str(g.get("kickoff_time")),str(g.get("game_id"))))


def _players(directory: Path, games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows=_load(directory/"player_stats.json",[]); expanded=[]
    for row in rows:
        copy=dict(row); copy.setdefault("player_id",copy.get("canonical_player_id") or copy.get("athlete_id")); copy.setdefault("player_name",copy.get("player") or copy.get("name"))
        if copy.get("game_id"): expanded.append(copy)
        else:
            for game in games:
                if copy.get("team") in {game.get("home_team"),game.get("away_team")}: expanded.append({**copy,"game_id":game.get("game_id")})
    for game in games:
        for player in game.get("players",[]) or []: expanded.append({**player,"game_id":game.get("game_id")})
    return expanded


def _atomic_json(path: Path, value: Any) -> bool:
    content=json.dumps(value,indent=2,sort_keys=True)+"\n"
    if path.exists() and path.read_text(encoding="utf-8")==content: return False
    path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(content,encoding="utf-8"); tmp.replace(path); return True


def persist_week(directory: Path, rows: list[dict[str, Any]]) -> None:
    rows=sorted(rows,key=lambda r:tuple(str(r.get(k,"")) for k in ("game_id","canonical_player_id","market","bookmaker","line","selection","provider_snapshot_timestamp")))
    target=directory/"player_prop_odds.json"; _atomic_json(target,rows)
    digest=hashlib.sha256(target.read_bytes()).hexdigest(); manifest_path=directory/"manifest.json"
    manifest=_load(manifest_path,{"datasets":{}}); timestamps=sorted({r.get("provider_snapshot_timestamp") for r in rows if r.get("provider_snapshot_timestamp")})
    entry={"present":True,"status":"complete" if rows else "optional_empty","records":len(rows),"row_count":len(rows),"sha256":digest,
        "source":"the-odds-api-historical","provider":"the-odds-api","requested_snapshot_timestamps":sorted({r.get("requested_snapshot_timestamp") for r in rows if r.get("requested_snapshot_timestamp")}),
        "provider_snapshot_timestamps":timestamps,"markets":sorted({r["market"] for r in rows}),"bookmakers":sorted({r["bookmaker"] for r in rows})}
    manifest.setdefault("datasets",{})["player_prop_odds"]=entry
    manifest.setdefault("source_versions",{})["player_prop_odds"]=entry["source"]
    manifest.setdefault("source_lineage",{})["player_prop_odds"]={"provider":entry["provider"],"records":len(rows),"timestamp_fields":["requested_snapshot_timestamp","provider_snapshot_timestamp","market_last_update","captured_at","data_as_of"]}
    _atomic_json(manifest_path,manifest)


def execute(plan: dict[str, Any], root: Path, cache_root: Path, *, season: int,
            allow_paid: bool, resume: bool, validate: bool) -> dict[str, Any]:
    budget=int(plan["total_paid_request_budget"]); paid=0; all_rejected=[]; weekly={}; event_coverage={}
    key=os.getenv("THE_ODDS_API_KEY") or os.getenv("ODDS_API_KEY")
    cache=JsonRawCache(cache_root)
    games_by_week={}
    for rec in plan["per_game_requests"]: games_by_week.setdefault(rec["week"],[]).append(rec)
    for week,records in sorted(games_by_week.items()):
        directory=snapshot_week_dir(root,"nfl",season,week); games=load_registry(root,season,week,week); players=_players(directory,games); rows=[]
        for rec in records:
            game=next(g for g in games if str(g.get("game_id"))==str(rec["game_id"])); params=request_params(rec["provider_event_id"],rec["requested_snapshot_timestamp"],rec["markets"])
            path=Path(rec["cache_path"]); state,payload,errors=inspect_cache(path,event_id=rec["provider_event_id"],requested_at=rec["requested_snapshot_timestamp"],keys=rec["markets"])
            if state != "valid":
                if not allow_paid: raise ProviderUnavailable("paid fetch required; review plan and pass --allow-paid-fetch")
                if paid+1>budget: raise PaidBudgetExceeded(f"STOP: revised paid requests={paid+1}, reviewed budget={budget}; rerun and re-authorize")
                if not key: raise ProviderUnavailable("THE_ODDS_API_KEY/ODDS_API_KEY is not set")
                query={k:v for k,v in params.items() if k != "event_id"}; query["apiKey"]=key
                url=f"{ODDS_API_BASE}/historical/sports/{NFL_SPORT_KEY}/events/{rec['provider_event_id']}/odds?"+urlencode(query)
                print(f"Paid request {paid+1}/{budget}: {_redact_url(url)}")
                payload=cache.get_or_fetch("odds-api","nfl",season,week,"event-player-props",params,lambda:_fetch_json(url),overwrite=state=="invalid",replacement_reason="validated player-prop cache replacement")
                paid+=1
            event=payload["data"]; normalized,rejected=normalize_provider_outcomes(event,league="nfl",season=season,week=week,game_id=str(game["game_id"]),canonical_players=players,
                snapshot_timestamp=payload["timestamp"],requested_snapshot_timestamp=rec["requested_snapshot_timestamp"])
            raw_counts={}
            for book in event.get("bookmakers",[]) or []:
                for market in book.get("markets",[]) or []:
                    key=str(market.get("key")); raw_counts[key]=raw_counts.get(key,0)+len(market.get("outcomes",[]) or [])
            normalized_counts={}
            for quote in normalized: normalized_counts[quote["provider_market"]]=normalized_counts.get(quote["provider_market"],0)+1
            # Leakage and pair integrity are hard persistence boundaries.
            from .player_prop_odds import filter_player_quotes
            eligible,diag=filter_player_quotes(game,normalized)
            # The provider envelope is an archive-record identity, while the
            # requested timestamp is the as-of safety boundary.
            from .game_matching import parse_dt
            safe=[]
            for quote in eligible:
                update=parse_dt(quote.get("market_last_update")); requested=parse_dt(quote.get("requested_snapshot_timestamp"))
                provider=parse_dt(quote.get("provider_snapshot_timestamp"))
                reason = "future_snapshot" if provider and requested and provider > requested else \
                         "invalid_market_timestamp" if not update or (requested and update > requested) else None
                if reason: rejected.append({"reason":reason,"quote":quote})
                else: safe.append(quote)
            eligible,dedup=deduplicate_quotes(safe)
            rejected.extend({"reason":"duplicate_exact"} for _ in range(dedup["duplicate_exact"]))
            rejected.extend({"reason":"duplicate_conflict","detail":d} for d in dedup["conflicts"])
            complete={p["key"] for p in pair_quotes(eligible) if p["complete"]}
            incomplete=[q for q in eligible if tuple(q.get(k) for k in ("game_id","canonical_player_id","market","bookmaker","line","snapshot_timestamp")) not in complete]
            rejected.extend({"reason":"incomplete_over_under_pair","quote":q} for q in incomplete)
            rows.extend(q for q in eligible if q not in incomplete); all_rejected.extend(rejected)
            persisted=[q for q in eligible if q not in incomplete]
            persisted_counts={}
            for quote in persisted: persisted_counts[quote["provider_market"]]=persisted_counts.get(quote["provider_market"],0)+1
            reasons={}
            for item in rejected:
                key=str(item.get("market") or (item.get("quote") or {}).get("provider_market") or "unknown")
                reasons.setdefault(key,{}); reasons[key][item["reason"]]=reasons[key].get(item["reason"],0)+1
            event_coverage[rec["provider_event_id"]]={"raw_provider":dict(sorted(raw_counts.items())),
                "normalized":dict(sorted(normalized_counts.items())),"persisted":dict(sorted(persisted_counts.items())),
                "rejections":reasons}
        errors=validate_player_prop_rows(rows,games,players)
        if errors and validate: raise ValueError("player_prop_odds validation failed:\n"+"\n".join(errors))
        counters={}
        for item in all_rejected: counters[item["reason"]]=counters.get(item["reason"],0)+1
        persist_week(directory,rows); weekly[week]={"quotes":len(rows),"rejected":len(all_rejected),"rejection_counters":counters,"validation_errors":errors}
    return {"network_contacted":bool(paid),"paid_requests_made":paid,"paid_request_budget":budget,
            "weeks":weekly,"event_coverage":event_coverage,"rejected":all_rejected}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--season",type=int,required=True); p.add_argument("--start-week",type=int,required=True); p.add_argument("--end-week",type=int,required=True)
    p.add_argument("--snapshot-root",type=Path,default=SNAPSHOTS_DIR); p.add_argument("--cache-root",type=Path); p.add_argument("--plan",action="store_true"); p.add_argument("--resume",action="store_true"); p.add_argument("--rebuild-from-cache",action="store_true",help="require validated cache hits and prohibit every network request"); p.add_argument("--validate",action="store_true"); p.add_argument("--allow-paid-fetch",action="store_true")
    a=p.parse_args(argv)
    if a.end_week<a.start_week: p.error("end-week must be >= start-week")
    if a.rebuild_from_cache and a.allow_paid_fetch: p.error("--rebuild-from-cache cannot be combined with --allow-paid-fetch")
    if a.allow_paid_fetch and (a.start_week!=1 or a.end_week!=1): p.error("first paid pilot is restricted to Week 1")
    cache_root=a.cache_root or a.snapshot_root.parent/"raw_cache"; games=load_registry(a.snapshot_root,a.season,a.start_week,a.end_week)
    plan=plan_acquisition(games,cache_root,season=a.season); print(json.dumps(plan,indent=2,sort_keys=True))
    if a.plan: return 0
    result=execute(plan,a.snapshot_root,cache_root,season=a.season,allow_paid=a.allow_paid_fetch,resume=a.resume,validate=a.validate); print(json.dumps(result,indent=2,sort_keys=True)); return 0

if __name__ == "__main__": raise SystemExit(main())
