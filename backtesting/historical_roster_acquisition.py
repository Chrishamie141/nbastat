"""Cache-first acquisition of point-in-time NFL roster identity evidence.

ESPN's public team-roster route is retained for verification, but is not a
historical-roster provider: its ``season`` query is not defensible effective-date
or week scope.  Post-cutoff responses are rejected unless another provider/cache
supplies explicit historical scope.

Timestamp semantics are intentionally separate: ``captured_at`` records when
our system downloaded an artifact, ``provider_effective_at``/``historical_scope``
record the period represented by the provider, and ``known_at`` is the validated
applicability timestamp consumed by leakage checks.  A late download can only use
the latter when deterministic provider scope validation succeeds.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlencode

from nfl_providers import (ESPN_ROSTER, HttpJsonResponse, JsonRawCache,
                           StructuredHttpError, _fetch_json_structured, _redact_url)
from .game_matching import normalize_team, parse_dt

PROVIDER = "espn"
ENDPOINT = "team-roster"
ESPN_HISTORICAL_ROSTERS_SUPPORTED = False
UNSUPPORTED_REASON = ("ESPN team roster responses do not declare a roster week or effective date; "
                      "the season query alone is not defensible historical scope. An external "
                      "point-in-time roster archive is required.")


def roster_params(team: str, season: int) -> dict[str, Any]:
    return {"team": normalize_team(team), "season": int(season)}


def roster_url(team: str, season: int) -> str:
    # Most ESPN slugs equal our canonical abbreviation. Washington is the
    # provider-specific exception: ESPN rejects ``was`` and accepts ``wsh``.
    canonical = normalize_team(team)
    provider_slug = {"WAS": "wsh"}.get(canonical, canonical.lower())
    return f"{ESPN_ROSTER}/{provider_slug}/roster?" + urlencode({"season": int(season)})


def roster_cache_path(cache_root: Path, season: int, week: int, team: str) -> Path:
    return JsonRawCache(cache_root).path(PROVIDER, "nfl", season, week, ENDPOINT,
                                         roster_params(team, season))


def _cutoff(game: dict[str, Any]):
    return parse_dt(game.get("prediction_cutoff") or game.get("snapshot_time") or game.get("kickoff_time"))


def _athletes(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict): return []
    rows=[]
    for item in payload.get("athletes", []) or []:
        if isinstance(item, dict) and isinstance(item.get("items"), list):
            rows.extend(x for x in item["items"] if isinstance(x, dict))
        elif isinstance(item, dict): rows.append(item)
    return rows


def discover_historical_scope(payload: Any, metadata: dict[str, Any]) -> dict[str, Any] | None:
    """Return only explicit provider scope, never scope inferred from the request URL.

    Cache importers for a genuine archive may persist ``provider_historical_scope``.
    ESPN's response-level ``season`` object is deliberately not enough: tests against
    the public route have not established point-in-time membership semantics.
    """
    scope = metadata.get("provider_historical_scope")
    if not isinstance(scope, dict) or not scope.get("source_field"): return None
    return {k: scope.get(k) for k in ("season", "week", "effective_at", "roster_date", "source_field")
            if scope.get(k) is not None}


def _scope_errors(metadata: dict[str, Any], request: dict[str, Any]) -> tuple[list[str], dict[str, Any] | None]:
    errors=[]; captured=metadata.get("request_timestamp"); captured_dt=parse_dt(captured)
    cutoff=parse_dt(request.get("historical_cutoff")); scope=discover_historical_scope(None, metadata)
    if not captured_dt: errors.append("missing_captured_at")
    if not cutoff: errors.append("missing_historical_cutoff")
    late_capture=bool(captured_dt and cutoff and captured_dt > cutoff)
    if late_capture and not scope:
        errors.extend(["captured_after_historical_cutoff", "missing_provider_historical_scope"])
    if scope:
        try: scope_season=int(scope.get("season", -1))
        except (TypeError, ValueError): scope_season=-1
        if scope_season != int(request["season"]): errors.append("provider_season_scope_mismatch")
        if scope.get("week") is not None:
            try: week_matches=int(scope["week"]) == int(request["week"])
            except (TypeError,ValueError): week_matches=False
            if not week_matches: errors.append("provider_week_scope_mismatch")
        effective_value=scope.get("effective_at") or scope.get("roster_date")
        effective=parse_dt(effective_value)
        if effective_value and not effective: errors.append("malformed_provider_effective_at")
        if scope.get("week") is None and not effective:
            errors.append("provider_scope_missing_week_or_effective_at")
        if effective and cutoff and effective > cutoff: errors.append("provider_effective_at_after_cutoff")
    return list(dict.fromkeys(errors)),scope


def normalize_cached_roster(payload: Any, metadata: dict[str, Any], request: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Normalize identity when contemporaneous capture or explicit scope proves safety."""
    errors,scope=_scope_errors(metadata,request)
    identity=((metadata.get("request_identity") or {}).get("params") or {})
    try: identity_season=int(identity.get("season", -1))
    except (TypeError,ValueError): identity_season=-1
    if identity_season != int(request["season"]): errors.append("season_scope_mismatch")
    if normalize_team(identity.get("team")) != request["team"]: errors.append("team_scope_mismatch")
    if errors: return [],list(dict.fromkeys(errors))
    captured=metadata.get("request_timestamp")
    provider_effective=(scope or {}).get("effective_at") or (scope or {}).get("roster_date")
    effective=provider_effective or captured
    # For an exact season/week archive without a provider timestamp, the game
    # cutoff is the deterministic applicability boundary.  The later capture
    # remains separately auditable and must never become the leakage timestamp.
    known_at=(provider_effective or request.get("historical_cutoff")) if scope else captured
    validation_method=("provider_effective_at" if provider_effective else "provider_season_week") if scope else "capture_timestamp"
    rows=[]
    for athlete in _athletes(payload):
        name=athlete.get("displayName") or athlete.get("fullName")
        if not name: continue
        position=athlete.get("position")
        if isinstance(position,dict): position=position.get("abbreviation") or position.get("name")
        rows.append({"player_name":str(name),"provider_player_id":str(athlete.get("id")) if athlete.get("id") is not None else None,
            "team":request["team"],"game_id":request["game_id"],"season":request["season"],"week":request["week"],
            "position":position,"source":"historical_roster","provider":metadata.get("provider",PROVIDER),
            "effective_context":"provider-scoped historical roster" if scope else "contemporaneously captured roster",
            "historical_scope":scope,"provider_effective_at":provider_effective,
            "scope_validation_method":validation_method,"captured_at":captured,
            "known_at":known_at,"data_as_of":effective})
    return sorted(rows,key=lambda r:(r["team"],r["player_name"],str(r["provider_player_id"] or ""))),[]


def plan_roster_acquisition(games: Iterable[dict[str, Any]], cache_root: Path, *, season: int) -> dict[str, Any]:
    """Describe every request without creating files or contacting ESPN."""
    records=[]
    for game in sorted(games,key=lambda g:(int(g.get("week",0)),str(g.get("game_id")))):
        for team in sorted({normalize_team(game.get("home_team")),normalize_team(game.get("away_team"))}-{""}):
            week=int(game.get("week") or 0); path=roster_cache_path(cache_root,season,week,team)
            provisional={"game_id":str(game.get("game_id")),"team":team,"season":season,"week":week,
                "historical_cutoff":(_cutoff(game).isoformat().replace("+00:00","Z") if _cutoff(game) else None)}
            errors=[]
            if path.exists():
                try:
                    payload=json.loads(path.read_text()); metadata=json.loads(path.with_suffix(".metadata.json").read_text())
                    normalized,errors=normalize_cached_roster(payload,metadata,provisional)
                    if not errors and not normalized: errors.append("empty_roster_identity_response")
                except (OSError,json.JSONDecodeError): errors=["missing_or_malformed_cache"]
            state="missing" if not path.exists() else "invalid" if errors else "cached"
            records.append({**provisional,"cache_path":str(path),"cache_state":state,"cache_errors":errors,
                "cache_hit":state=="cached","requires_network":state!="cached"})
    missing=sum(r["requires_network"] for r in records)
    return {"provider":PROVIDER,"endpoint":ENDPOINT,"historical_acquisition_supported":False,
        "unsupported_reason":UNSUPPORTED_REASON,"network_contacted":False,"requests_required":missing,
        "cache_hits":sum(r["cache_hit"] for r in records),"missing_coverage":missing,
        "network_required":bool(missing),"paid_quota_estimate":0,
        "request_cost":"free ESPN endpoint; network requires explicit opt-in",
        "per_game_team_requests":records}


def _http_failure(error: StructuredHttpError, request: dict[str, Any]) -> dict[str, Any]:
    reason="invalid_provider_response" if error.classification=="INVALID_PROVIDER_RESPONSE" else "http_error"
    return {**request,"reason":reason,"http_status":error.status,
            "classification":error.classification,"url":error.redacted_url,
            "provider_message":error.provider_message,"response_headers":error.headers}


def acquire_roster_identities(plan: dict[str, Any], *, allow_network: bool=False,
                              refresh: bool=False,
                              fetcher: Callable[..., Any]=_fetch_json_structured) -> tuple[list[dict[str, Any]],dict[str,Any]]:
    """Resume request-by-request; a provider error cannot discard prior cache writes."""
    rows=[]; rejected=[]; failed=[]; written=[]; cached=[]; refreshed=[]; succeeded=[]; network=False
    for request in plan["per_game_team_requests"]:
        path=Path(request["cache_path"]); cache=JsonRawCache(path.parents[4])
        if request["cache_state"] == "cached" and not refresh: cached.append(request["team"])
        else:
            if not allow_network:
                rejected.append({**request,"reason":"network_opt_in_required"}); continue
            cutoff=parse_dt(request.get("historical_cutoff"))
            if (not plan.get("historical_acquisition_supported",False) and cutoff and
                    cutoff < datetime.now(timezone.utc)):
                rejected.append({**request,"reason":"provider_historical_acquisition_unsupported",
                                 "detail":UNSUPPORTED_REASON})
                continue
            url=roster_url(request["team"],request["season"]); network=True
            try:
                cache.get_or_fetch(PROVIDER,"nfl",request["season"],request["week"],ENDPOINT,
                    roster_params(request["team"],request["season"]),lambda:fetcher(url),
                    overwrite=refresh or request["cache_state"]=="invalid",
                    replacement_reason=("explicit contemporaneous roster refresh" if refresh else
                                        "invalid roster cache replacement"))
                written.append(str(path)); succeeded.append(request["team"])
                if refresh and request["cache_state"] == "cached": refreshed.append(request["team"])
            except StructuredHttpError as error:
                failed.append(_http_failure(error,request)); continue
        try:
            payload=json.loads(path.read_text()); metadata=json.loads(path.with_suffix(".metadata.json").read_text())
        except (OSError,json.JSONDecodeError):
            rejected.append({**request,"reason":"invalid_cache"}); continue
        normalized,errors=normalize_cached_roster(payload,metadata,request)
        if errors: rejected.append({**request,"reason":"historical_scope_rejected","errors":errors})
        rows.extend(normalized)
    unique={(r["game_id"],r["team"],r["player_name"],r.get("provider_player_id")):r for r in rows}
    result=sorted(unique.values(),key=lambda r:(r["game_id"],r["team"],r["player_name"],str(r.get("provider_player_id") or "")))
    report={"provider":PROVIDER,"historical_acquisition_supported":bool(plan.get("historical_acquisition_supported")),
        "unsupported_reason":None if plan.get("historical_acquisition_supported") else UNSUPPORTED_REASON,
        "network_contacted":network,"raw_cache_files_written":written,"succeeded":succeeded,"failed":failed,
        "rejected":rejected,"cached":cached,"refreshed":refreshed,
        "counts":{"succeeded":len(succeeded),"failed":len(failed),
        "rejected":len(rejected),"cached":len(cached)},"identities_acquired":len(result),
        "identities_with_provider_id":sum(bool(r.get("provider_player_id")) for r in result),
        "teams_weeks_covered":sorted({f"{r['season']}|{r['week']}|{r['team']}" for r in result}),
        "rejected_coverage":rejected+failed}
    return result,report


def verify_single_request(request: dict[str,Any], *, fetcher: Callable[...,Any]=_fetch_json_structured) -> dict[str,Any]:
    """Perform exactly one uncached request and explain its historical validity."""
    url=roster_url(request["team"],request["season"])
    try:
        response=fetcher(url)
    except StructuredHttpError as error:
        return {"provider":PROVIDER,"url":error.redacted_url,"http_status":error.status,
            "classification":error.classification,"content_type":error.headers.get("content-type"),
            "content_encoding":error.headers.get("content-encoding"),"historical_scope":None,
            "athlete_count":0,"acceptance":False,"acceptable":False,
            "rejection_reason":error.provider_message}
    if not isinstance(response,HttpJsonResponse): response=HttpJsonResponse(response,200,{})
    metadata={"request_timestamp":None,"request_identity":{"params":roster_params(request["team"],request["season"])}}
    scope=discover_historical_scope(response.payload,metadata)
    return {"provider":PROVIDER,"url":_redact_url(url),"http_status":response.status,
        "classification":"SUCCESS","content_type":response.headers.get("content-type"),
        "content_encoding":response.headers.get("content-encoding"),"historical_scope":scope,
        "athlete_count":len(_athletes(response.payload)),"acceptance":False,"acceptable":False,
        "rejection_reason":UNSUPPORTED_REASON}


def _write(path:Path,value:Any)->None:
    content=json.dumps(value,indent=2,sort_keys=True)+"\n"
    if path.exists() and path.read_text()==content:return
    path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(content); tmp.replace(path)


def main(argv=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season",type=int,required=True); parser.add_argument("--week",type=int,required=True)
    parser.add_argument("--snapshot-root",type=Path,default=Path("backtesting/data/snapshots"))
    parser.add_argument("--cache-root",type=Path,default=Path("backtesting/data/raw_cache"))
    parser.add_argument("--plan",action="store_true"); parser.add_argument("--allow-network",action="store_true")
    parser.add_argument("--refresh",action="store_true",
                        help="replace valid cached rosters with a new contemporaneous capture")
    parser.add_argument("--verify-team",help="make one uncached ESPN roster request for this team")
    args=parser.parse_args(argv); directory=args.snapshot_root/"nfl"/str(args.season)/f"week_{args.week:02d}"
    try: games=json.loads((directory/"games.json").read_text())
    except (OSError,json.JSONDecodeError) as error: parser.error(f"cannot load games snapshot: {error}")
    plan=plan_roster_acquisition(games,args.cache_root,season=args.season)
    if args.verify_team:
        matches=[r for r in plan["per_game_team_requests"] if r["team"]==normalize_team(args.verify_team)]
        if not matches: parser.error("verification team is not present in the requested week")
        print(json.dumps(verify_single_request(matches[0]),indent=2,sort_keys=True)); return 0
    if args.plan: print(json.dumps(plan,indent=2,sort_keys=True)); return 0
    rows,report=acquire_roster_identities(plan,allow_network=args.allow_network,refresh=args.refresh)
    _write(directory/"roster_identities.json",rows); print(json.dumps(report,indent=2,sort_keys=True))
    return 0 if not report["rejected_coverage"] else 2


if __name__=="__main__":raise SystemExit(main())
