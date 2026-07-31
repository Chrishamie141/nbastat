"""Cache-first acquisition of point-in-time ESPN NFL roster identity evidence.

ESPN is already the project's schedule/statistics provider.  Its team roster
endpoint is useful only when captured no later than the replay cutoff: a season
query made after a historical game is *not* treated as a historical archive.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlencode

from nfl_providers import ESPN_ROSTER, JsonRawCache, _fetch_json_structured
from .game_matching import normalize_team, parse_dt

PROVIDER = "espn"
ENDPOINT = "team-roster"


def roster_params(team: str, season: int) -> dict[str, Any]:
    return {"team": normalize_team(team), "season": int(season)}


def roster_cache_path(cache_root: Path, season: int, week: int, team: str) -> Path:
    return JsonRawCache(cache_root).path(PROVIDER, "nfl", season, week, ENDPOINT,
                                         roster_params(team, season))


def _cutoff(game: dict[str, Any]):
    return parse_dt(game.get("prediction_cutoff") or game.get("snapshot_time") or
                    game.get("kickoff_time"))


def plan_roster_acquisition(games: Iterable[dict[str, Any]], cache_root: Path,
                            *, season: int) -> dict[str, Any]:
    """Describe every request without creating files or contacting ESPN."""
    records=[]
    for game in sorted(games, key=lambda g: (int(g.get("week", 0)), str(g.get("game_id")))):
        for team in sorted({normalize_team(game.get("home_team")), normalize_team(game.get("away_team"))} - {""}):
            week=int(game.get("week") or 0); path=roster_cache_path(cache_root,season,week,team)
            payload=metadata=None; errors=[]
            if path.exists():
                try: payload=json.loads(path.read_text(encoding="utf-8"))
                except (OSError,json.JSONDecodeError): errors.append("malformed_json")
                meta_path=path.with_suffix(".metadata.json")
                try: metadata=json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError,json.JSONDecodeError): errors.append("missing_or_malformed_metadata")
            provisional={"game_id":str(game.get("game_id")),"team":team,"season":season,"week":week,
                "historical_cutoff":(_cutoff(game).isoformat().replace("+00:00","Z") if _cutoff(game) else None)}
            if payload is not None and metadata is not None:
                normalized,scope_errors=normalize_cached_roster(payload,metadata,provisional)
                errors.extend(scope_errors)
                if not scope_errors and not normalized: errors.append("empty_roster_identity_response")
            state="missing" if not path.exists() else "invalid" if errors else "cached"
            records.append({**provisional,
                "cache_path":str(path),"cache_state":state,"cache_errors":errors,
                "cache_hit":state=="cached","requires_network":state!="cached"})
    # A team can appear only once per week, but retain game identity in the plan
    # so normalized evidence is explicitly game scoped.
    missing=sum(r["requires_network"] for r in records)
    return {"provider":PROVIDER,"endpoint":ENDPOINT,"network_contacted":False,
        "requests_required":missing,"cache_hits":sum(r["cache_hit"] for r in records),
        "missing_coverage":missing,"network_required":bool(missing),"paid_quota_estimate":0,
        "request_cost":"free ESPN endpoint; network still requires explicit opt-in",
        "per_game_team_requests":records}


def _athletes(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload,dict): return []
    rows=[]
    for item in payload.get("athletes",[]) or []:
        if isinstance(item,dict) and isinstance(item.get("items"),list): rows.extend(x for x in item["items"] if isinstance(x,dict))
        elif isinstance(item,dict): rows.append(item)
    return rows


def normalize_cached_roster(payload: Any, metadata: dict[str, Any], request: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Normalize identity only, rejecting evidence captured after its cutoff."""
    errors=[]; captured=metadata.get("request_timestamp"); captured_dt=parse_dt(captured)
    cutoff=parse_dt(request.get("historical_cutoff"))
    identity=((metadata.get("request_identity") or {}).get("params") or {})
    if not captured_dt: errors.append("missing_captured_at")
    if not cutoff: errors.append("missing_historical_cutoff")
    if captured_dt and cutoff and captured_dt > cutoff: errors.append("captured_after_historical_cutoff")
    if int(identity.get("season",-1)) != int(request["season"]): errors.append("season_scope_mismatch")
    if normalize_team(identity.get("team")) != request["team"]: errors.append("team_scope_mismatch")
    if errors: return [],errors
    rows=[]
    for athlete in _athletes(payload):
        name=athlete.get("displayName") or athlete.get("fullName")
        if not name: continue
        position=athlete.get("position")
        if isinstance(position,dict): position=position.get("abbreviation") or position.get("name")
        rows.append({"player_name":str(name),"provider_player_id":str(athlete.get("id")) if athlete.get("id") is not None else None,
            "team":request["team"],"game_id":request["game_id"],"season":request["season"],"week":request["week"],
            "position":position,"source":"historical_roster","provider":PROVIDER,
            "effective_context":"season roster captured for game-week reconciliation",
            "captured_at":captured,"known_at":captured,"data_as_of":captured})
    return sorted(rows,key=lambda r:(r["team"],r["player_name"],str(r["provider_player_id"] or ""))),[]


def acquire_roster_identities(plan: dict[str, Any], *, allow_network: bool=False,
                              fetcher: Callable[..., Any]=_fetch_json_structured) -> tuple[list[dict[str, Any]],dict[str,Any]]:
    """Use valid cache entries, optionally fetching missing *current-time* evidence."""
    rows=[]; rejected=[]; network=False; written=[]
    for request in plan["per_game_team_requests"]:
        path=Path(request["cache_path"]); cache=JsonRawCache(path.parents[4])
        if request["cache_state"] != "cached":
            if not allow_network:
                rejected.append({**request,"reason":"network_opt_in_required"}); continue
            params=roster_params(request["team"],request["season"])
            url=f"{ESPN_ROSTER}/{request['team']}/roster?"+urlencode({"season":request["season"]})
            cache.get_or_fetch(PROVIDER,"nfl",request["season"],request["week"],ENDPOINT,params,lambda:fetcher(url),
                               overwrite=request["cache_state"]=="invalid",
                               replacement_reason="invalid historical roster cache replacement")
            network=True; written.append(str(path))
        try:
            payload=json.loads(path.read_text(encoding="utf-8")); metadata=json.loads(path.with_suffix(".metadata.json").read_text(encoding="utf-8"))
        except (OSError,json.JSONDecodeError):
            rejected.append({**request,"reason":"invalid_cache"}); continue
        normalized,errors=normalize_cached_roster(payload,metadata,request)
        if errors: rejected.append({**request,"reason":"historical_scope_rejected","errors":errors})
        rows.extend(normalized)
    unique={(r["game_id"],r["team"],r["player_name"],r.get("provider_player_id")):r for r in rows}
    result=sorted(unique.values(),key=lambda r:(r["game_id"],r["team"],r["player_name"],str(r.get("provider_player_id") or "")))
    report={"provider":PROVIDER,"network_contacted":network,"raw_cache_files_written":written,
        "identities_acquired":len(result),"identities_with_provider_id":sum(bool(r.get("provider_player_id")) for r in result),
        "teams_weeks_covered":sorted({f"{r['season']}|{r['week']}|{r['team']}" for r in result}),"rejected_coverage":rejected}
    return result,report


def _write(path: Path, value: Any) -> None:
    content=json.dumps(value,indent=2,sort_keys=True)+"\n"
    if path.exists() and path.read_text(encoding="utf-8")==content: return
    path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(content,encoding="utf-8"); tmp.replace(path)


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season",type=int,required=True); parser.add_argument("--week",type=int,required=True)
    parser.add_argument("--snapshot-root",type=Path,default=Path("backtesting/data/snapshots"))
    parser.add_argument("--cache-root",type=Path,default=Path("backtesting/data/raw_cache"))
    parser.add_argument("--plan",action="store_true"); parser.add_argument("--allow-network",action="store_true")
    args=parser.parse_args(argv); directory=args.snapshot_root/"nfl"/str(args.season)/f"week_{args.week:02d}"
    try: games=json.loads((directory/"games.json").read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as error: parser.error(f"cannot load games snapshot: {error}")
    plan=plan_roster_acquisition(games,args.cache_root,season=args.season)
    if args.plan:
        print(json.dumps(plan,indent=2,sort_keys=True)); return 0
    rows,report=acquire_roster_identities(plan,allow_network=args.allow_network)
    _write(directory/"roster_identities.json",rows)
    print(json.dumps(report,indent=2,sort_keys=True))
    return 0 if not report["rejected_coverage"] else 2


if __name__ == "__main__": raise SystemExit(main())
