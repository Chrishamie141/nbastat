"""Plan or execute quota-safe historical NFL player-prop ingestion."""
from __future__ import annotations

import argparse, hashlib, json, os, time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from nfl_providers import (JsonRawCache, NFL_SPORT_KEY, ODDS_API_BASE, ProviderUnavailable,
                           StructuredHttpError, _fetch_json_structured, _redact_url)
from .config import SNAPSHOTS_DIR
from .player_prop_acquisition import inspect_cache, plan_acquisition, request_params
from .player_prop_odds import (deduplicate_quotes, normalize_provider_outcomes, pair_quotes,
                               validate_player_prop_rows)
from .snapshots import snapshot_week_dir
from .player_identity_registry import build_identity_registry, registry_diagnostics


class PaidBudgetExceeded(RuntimeError): pass

PLAYER_PROP_MAX_RETRIES = int(os.getenv("PLAYER_PROP_MAX_RETRIES", "2"))
PLAYER_PROP_RETRY_BACKOFF_SECONDS = float(os.getenv("PLAYER_PROP_RETRY_BACKOFF_SECONDS", "1"))
PLAYER_PROP_CREDITS_PER_REQUEST = 60


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


def _players(directory: Path, games: list[dict[str, Any]], *, season: int | None = None,
             week: int | None = None, cache_root: Path | None = None) -> list[dict[str, Any]]:
    """Compatibility wrapper for the independent, persisted identity registry."""
    season=int(season or next((g.get("season") for g in games if g.get("season")),0))
    week=int(week or next((g.get("week") for g in games if g.get("week")),0))
    return build_identity_registry(directory,games,season=season,week=week,cache_root=cache_root)


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
            allow_paid: bool, resume: bool, validate: bool, fail_fast: bool = False,
            sleeper=time.sleep) -> dict[str, Any]:
    budget=int(plan["total_paid_request_budget"]); paid=0; all_rejected=[]; weekly={}; event_coverage={}
    report={"status":"SUCCESS","planned_events":len(plan["per_game_requests"]),
        "validated_cache_hits":0,"paid_attempts":0,"paid_successes":0,"paid_failures":0,
        "failed_events":[],"remaining_events":0,"estimated_credits_already_attempted":0,
        "estimated_remaining_credit_exposure":int(plan.get("estimated_credits",budget*PLAYER_PROP_CREDITS_PER_REQUEST)),
        "network_contacted":False,"raw_cache_files_written":[],"resume_safe":True,
        "provider_diagnostics":[]}
    api_key=os.getenv("THE_ODDS_API_KEY") or os.getenv("ODDS_API_KEY")
    cache=JsonRawCache(cache_root)
    games_by_week={}
    for rec in plan["per_game_requests"]: games_by_week.setdefault(rec["week"],[]).append(rec)
    for week,records in sorted(games_by_week.items()):
        directory=snapshot_week_dir(root,"nfl",season,week); games=load_registry(root,season,week,week)
        players=_players(directory,games,season=season,week=week,cache_root=cache_root); rows=[]
        _atomic_json(directory/"player_identities.json",players)
        stop_paid=False
        for rec in records:
            game=next(g for g in games if str(g.get("game_id"))==str(rec["game_id"])); params=request_params(rec["provider_event_id"],rec["requested_snapshot_timestamp"],rec["markets"])
            path=Path(rec["cache_path"]); state,payload,errors=inspect_cache(path,event_id=rec["provider_event_id"],requested_at=rec["requested_snapshot_timestamp"],keys=rec["markets"])
            if state == "valid": report["validated_cache_hits"]+=1
            if state != "valid":
                if not allow_paid: raise ProviderUnavailable("paid fetch required; review plan and pass --allow-paid-fetch")
                if paid+1>budget: raise PaidBudgetExceeded(f"STOP: revised paid requests={paid+1}, reviewed budget={budget}; rerun and re-authorize")
                if not api_key: raise ProviderUnavailable("THE_ODDS_API_KEY/ODDS_API_KEY is not set")
                query={k:v for k,v in params.items() if k != "event_id"}; query["apiKey"]=api_key
                url=f"{ODDS_API_BASE}/historical/sports/{NFL_SPORT_KEY}/events/{rec['provider_event_id']}/odds?"+urlencode(query)
                print(f"Paid request {paid+1}/{budget}: {_redact_url(url)}")
                attempt=0
                def fetch():
                    nonlocal attempt, paid
                    attempt+=1; paid+=1
                    report["paid_attempts"]+=1; report["network_contacted"]=True
                    try:
                        response=_fetch_json_structured(url)
                        report["provider_diagnostics"].append({"event_id":rec["provider_event_id"],
                            "attempt":attempt,"http_status":response.status,"headers":response.headers})
                        return response
                    except StructuredHttpError as error:
                        diagnostic={"event_id":rec["provider_event_id"],"game_id":rec["game_id"],
                            "requested_snapshot_timestamp":rec["requested_snapshot_timestamp"],
                            "markets":list(rec["markets"]),"attempt":attempt,"paid_request_count":paid,
                            "http_status":error.status,"classification":error.classification,
                            "provider_message":error.provider_message,"redacted_url":error.redacted_url,
                            "headers":error.headers,"network_contacted":True}
                        report["provider_diagnostics"].append(diagnostic)
                        if error.classification not in {"RATE_LIMITED","TRANSIENT_PROVIDER_ERROR"} or attempt>PLAYER_PROP_MAX_RETRIES:
                            error.diagnostic=diagnostic
                            raise
                        retry_after=error.headers.get("retry-after")
                        try: delay=float(retry_after) if retry_after is not None else PLAYER_PROP_RETRY_BACKOFF_SECONDS*attempt
                        except ValueError: delay=PLAYER_PROP_RETRY_BACKOFF_SECONDS*attempt
                        sleeper(max(0,delay))
                try:
                    payload=cache.get_or_fetch("odds-api","nfl",season,week,"event-player-props",params,fetch,overwrite=state=="invalid",replacement_reason="validated player-prop cache replacement")
                except StructuredHttpError as error:
                    failure=dict(error.diagnostic); report["failed_events"].append(failure)
                    report["paid_failures"]+=1; report["status"]="PARTIAL"
                    stop_paid=error.classification=="AUTHENTICATION_OR_ENTITLEMENT"
                    if fail_fast or stop_paid: break
                    continue
                report["paid_successes"]+=1
                report["raw_cache_files_written"].append(str(path))
            data=payload.get("data",payload) if isinstance(payload,dict) else payload
            candidates=data if isinstance(data,list) else [data]
            matching=[e for e in candidates if isinstance(e,dict) and str(e.get("id") or e.get("event_id"))==str(rec["provider_event_id"])]
            if len(matching)!=1: raise ValueError(f"cache must contain exactly one event {rec['provider_event_id']}")
            event=matching[0]; envelope_timestamp=payload.get("timestamp") if isinstance(payload,dict) else None
            snapshot_timestamp=envelope_timestamp or rec["requested_snapshot_timestamp"]
            normalized,rejected=normalize_provider_outcomes(event,league="nfl",season=season,week=week,game_id=str(game["game_id"]),canonical_players=players,
                snapshot_timestamp=snapshot_timestamp,requested_snapshot_timestamp=rec["requested_snapshot_timestamp"])
            raw_counts={}
            for book in event.get("bookmakers",[]) or []:
                for market in book.get("markets",[]) or []:
                    market_key=str(market.get("key")); raw_counts[market_key]=raw_counts.get(market_key,0)+len(market.get("outcomes",[]) or [])
            normalized_counts={}
            for quote in normalized: normalized_counts[quote["provider_market"]]=normalized_counts.get(quote["provider_market"],0)+1
            # Leakage and pair integrity are hard persistence boundaries.
            from .player_prop_odds import filter_player_quotes
            eligible,diag=filter_player_quotes(game,normalized)
            eligible_objects={id(q) for q in eligible}
            for quote in normalized:
                if id(quote) not in eligible_objects:
                    from .team_history import market_quote_known_at
                    known=market_quote_known_at(quote)
                    reason="unknown_timestamp" if known is None else "timestamp_after_cutoff"
                    rejected.append({"reason":reason,"market":quote["market"],"quote":quote})
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
                market_key=str(item.get("market") or (item.get("quote") or {}).get("provider_market") or "unknown")
                reasons.setdefault(market_key,{}); reasons[market_key][item["reason"]]=reasons[market_key].get(item["reason"],0)+1
            canonical_raw={}; canonical_normalized={}; canonical_persisted={}
            from .markets import normalize_player_prop_market
            for market_key,count in raw_counts.items():
                canonical=normalize_player_prop_market(market_key)
                if canonical: canonical_raw[canonical]=canonical_raw.get(canonical,0)+count
            for market_key,count in normalized_counts.items():
                canonical=normalize_player_prop_market(market_key)
                if canonical: canonical_normalized[canonical]=canonical_normalized.get(canonical,0)+count
            for market_key,count in persisted_counts.items():
                canonical=normalize_player_prop_market(market_key)
                if canonical: canonical_persisted[canonical]=canonical_persisted.get(canonical,0)+count
            funnel={}
            for market in sorted(set(canonical_raw)|set(canonical_normalized)|set(canonical_persisted)):
                market_rejections={}
                for provider_key,counts in reasons.items():
                    if normalize_player_prop_market(provider_key)==market or provider_key==market:
                        for reason,count in counts.items(): market_rejections[reason]=market_rejections.get(reason,0)+count
                raw=canonical_raw.get(market,0); persisted_count=canonical_persisted.get(market,0)
                ambiguous=market_rejections.get("ambiguous_player",0); unknown=market_rejections.get("unknown_player",0)
                # Every row in a recognized provider market is normalized at
                # the market stage, before identity and value validation.
                normalized_count=raw; identity_matched=raw-ambiguous-unknown
                funnel[market]={"raw":raw,"normalized":normalized_count,"raw_rows":raw,"normalized_rows":normalized_count,"identity_matched":identity_matched,
                    "identity_ambiguous":ambiguous,"identity_unknown":unknown,
                    "timestamp_eligible":sum(q.get("market")==market for q in safe),
                    "deduplicated":sum(q.get("market")==market for q in eligible),
                    "paired":persisted_count,"persisted":persisted_count,"rejected":raw-persisted_count,
                    "rejections":dict(sorted(market_rejections.items()))}
            unknown_details=sorted({(str(item.get("player") or (item.get("quote") or {}).get("provider_player_name") or ""),
                                     str(item.get("market") or (item.get("quote") or {}).get("provider_market") or "unknown"))
                                    for item in rejected if item.get("reason")=="unknown_player"})
            event_coverage[rec["provider_event_id"]]={"game_id":str(game["game_id"]),"raw_provider":dict(sorted(raw_counts.items())),
                "normalized":dict(sorted(normalized_counts.items())),"persisted":dict(sorted(persisted_counts.items())),
                "funnel":funnel,"rejections":reasons,
                "unknown_players":[{"player_name":name,"market":market} for name,market in unknown_details]}
        errors=validate_player_prop_rows(rows,games,players)
        if errors and validate: raise ValueError("player_prop_odds validation failed:\n"+"\n".join(errors))
        counters={}
        for item in all_rejected: counters[item["reason"]]=counters.get(item["reason"],0)+1
        persist_week(directory,rows); weekly[week]={"quotes":len(rows),"rejected":len(all_rejected),"rejection_counters":counters,"validation_errors":errors}
        identity_diag=registry_diagnostics(players)
        roster_ids={r["canonical_player_id"] for r in players if not r.get("has_stats")}
        identity_diag.update({"prop_quotes_reconciled_via_roster_only_identity":sum(r.get("canonical_player_id") in roster_ids for r in rows),
            "reconciliation_method_counts":dict(sorted(Counter(r.get("reconciliation_method","UNKNOWN") for r in rows).items())),
            "remaining_unknown_players":sorted({str(x.get("player")) for x in all_rejected if x.get("reason")=="unknown_player" and x.get("player")}),
            "ambiguous_players":sorted({str(x.get("player")) for x in all_rejected if x.get("reason")=="ambiguous_player" and x.get("player")})})
        _atomic_json(directory/"player_prop_rebuild_audit.json",{"network_contacted":report["network_contacted"],"paid_requests_made":paid,
            "event_coverage":event_coverage,"rejection_counters":counters,**identity_diag})
        if stop_paid: break
    report["remaining_events"]=max(0,report["planned_events"]-report["validated_cache_hits"]-report["paid_successes"])
    report["estimated_credits_already_attempted"]=report["paid_attempts"]*PLAYER_PROP_CREDITS_PER_REQUEST
    report["estimated_remaining_credit_exposure"]=report["remaining_events"]*PLAYER_PROP_CREDITS_PER_REQUEST
    report.update({"paid_requests_made":paid,"paid_request_budget":budget,"requests_planned":report["planned_events"],
        "requests_attempted":report["paid_attempts"],"requests_succeeded":report["paid_successes"],
        "requests_failed":report["paid_failures"],"requests_served_from_cache":report["validated_cache_hits"],
        "weeks":weekly,"event_coverage":event_coverage,"rejected":all_rejected})
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--season",type=int,required=True); p.add_argument("--start-week",type=int,required=True); p.add_argument("--end-week",type=int,required=True)
    p.add_argument("--snapshot-root",type=Path,default=SNAPSHOTS_DIR); p.add_argument("--cache-root",type=Path); p.add_argument("--plan",action="store_true"); p.add_argument("--resume",action="store_true"); p.add_argument("--rebuild-from-cache",action="store_true",help="require validated cache hits and prohibit every network request"); p.add_argument("--validate",action="store_true"); p.add_argument("--allow-paid-fetch",action="store_true"); p.add_argument("--fail-fast",action="store_true")
    a=p.parse_args(argv)
    if a.end_week<a.start_week: p.error("end-week must be >= start-week")
    if a.rebuild_from_cache and a.allow_paid_fetch: p.error("--rebuild-from-cache cannot be combined with --allow-paid-fetch")
    if a.allow_paid_fetch and (a.start_week!=1 or a.end_week!=1): p.error("first paid pilot is restricted to Week 1")
    cache_root=a.cache_root or a.snapshot_root.parent/"raw_cache"; games=load_registry(a.snapshot_root,a.season,a.start_week,a.end_week)
    plan=plan_acquisition(games,cache_root,season=a.season); print(json.dumps(plan,indent=2,sort_keys=True))
    if a.plan: return 0
    try:
        result=execute(plan,a.snapshot_root,cache_root,season=a.season,allow_paid=a.allow_paid_fetch,resume=a.resume,validate=a.validate,fail_fast=a.fail_fast)
    except (ProviderUnavailable, PaidBudgetExceeded) as error:
        print(json.dumps({"status":"FATAL_CONFIGURATION","message":str(error),"resume_safe":True},sort_keys=True)); return 3
    except ValueError as error:
        print(json.dumps({"status":"VALIDATION_FAILURE","message":str(error),"resume_safe":True},sort_keys=True)); return 4
    print(json.dumps(result,indent=2,sort_keys=True))
    if result["status"]=="SUCCESS": return 0
    if any(f["classification"]=="AUTHENTICATION_OR_ENTITLEMENT" for f in result["failed_events"]): return 3
    return 2

if __name__ == "__main__": raise SystemExit(main())
