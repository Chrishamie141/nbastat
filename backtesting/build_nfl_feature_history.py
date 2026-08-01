"""Build free, ESPN-only completed NFL feature history snapshots.

This command is intentionally separate from the market snapshot builder: it
does not construct an Odds API provider and never writes odds as an input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from nfl_providers import EspnNflProvider, JsonRawCache, normalize_espn_player_boxscore

from .config import SNAPSHOTS_DIR
from .game_matching import parse_dt
from .outcomes import normalize_outcomes
from .snapshots import snapshot_week_dir

FEATURE_DATASETS = ("games", "player_stats", "team_stats", "outcomes", "injuries")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Build free ESPN NFL feature history (no odds).")
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--start-week", type=int, default=1)
    parser.add_argument("--end-week", type=int, default=18)
    parser.add_argument("--week", type=int, help="Rebuild one week (shorthand for equal start/end weeks).")
    parser.add_argument("--game-id", help="Only rebuild one ESPN game in the selected week.")
    parser.add_argument("--snapshot-root", type=Path, default=SNAPSHOTS_DIR)
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--rebuild-from-cache", action="store_true",
                        help="Re-normalize cached scoreboard/summary responses without network access.")
    args=parser.parse_args(argv)
    if args.week is not None: args.start_week=args.end_week=args.week
    return args


def _read_list(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _cached(provider: EspnNflProvider, season: int, week: int, endpoint: str,
            params: dict[str, Any]) -> bool:
    return provider.cache.path("espn", "nfl", season, week, endpoint, params).exists()


def make_plan(args, provider: EspnNflProvider) -> dict[str, Any]:
    weeks=[]; required=hits=0
    for week in range(args.start_week, args.end_week + 1):
        wdir=snapshot_week_dir(args.snapshot_root,"nfl",args.season,week)
        datasets={name: len(_read_list(wdir/f"{name}.json")) for name in FEATURE_DATASETS}
        score_params={"seasontype":2,"week":week,"dates":str(args.season)}
        score_path=provider.cache.path("espn","nfl",args.season,week,"scoreboard",score_params)
        score_hit=score_path.exists()
        games=_read_list(wdir/"games.json")
        if not games and score_hit:
            try:
                payload=json.loads(score_path.read_text())
                games=[provider.normalize_game(event,args.season,week)
                       for event in payload.get("events",[]) if isinstance(event,dict)]
            except (OSError, json.JSONDecodeError, AttributeError):
                games=[]
        needed=[] if games else [("scoreboard",score_params)]
        for game in games:
            event=str(game.get("espn_event_id") or str(game.get("game_id","")).removeprefix("espn-"))
            params={"event":event}
            if not _cached(provider,args.season,week,"summary",params): needed.append(("summary",params))
        # Existing complete player data means summaries need not be requested.
        if datasets["player_stats"]: needed=[item for item in needed if item[0] != "summary"]
        week_hits=int(score_hit)+sum(_cached(provider,args.season,week,e,p) for e,p in needed)
        hits += week_hits; required += sum(not _cached(provider,args.season,week,e,p) for e,p in needed)
        missing=[name for name,count in datasets.items() if not count and name != "injuries"]
        weeks.append({"week":week,"missing":missing,"datasets":datasets,
                      "free_requests_required":sum(not _cached(provider,args.season,week,e,p) for e,p in needed),
                      "cache_hits":week_hits})
    return {"season":args.season,"weeks":weeks,"missing_weeks":[w["week"] for w in weeks if w["missing"]],
            "free_requests_required":required,"cache_hits":hits,"network_contacted":False,
            "paid_requests_required":0,"estimated_paid_credits":0}


def _completed_at(game):
    kickoff=parse_dt(game.get("kickoff_time"))
    return (kickoff+timedelta(hours=6)).isoformat().replace("+00:00","Z") if kickoff else None


def _team_rows(games, outcomes):
    scores={row["game_id"]:row for row in outcomes}; rows=[]
    for game in games:
        outcome=scores.get(game.get("game_id"))
        if not outcome: continue
        completed=_completed_at(game) or outcome.get("completed_at")
        pairs=((game.get("home_team"),game.get("away_team"),outcome.get("final_home_score"),outcome.get("final_away_score"),"home"),
               (game.get("away_team"),game.get("home_team"),outcome.get("final_away_score"),outcome.get("final_home_score"),"away"))
        for team,opponent,pf,pa,side in pairs:
            rows.append({"season":int(game["season"]),"week":int(game["week"]),"through_week":int(game["week"]),
                "game_id":game["game_id"],"team":team,"opponent":opponent,"points_for":pf,"points_against":pa,
                "home_away":side,"completed_at":completed,"captured_at":completed,"data_as_of":completed,
                "record_role":"completed_game_history","is_pregame":False,"source":"espn-scoreboard"})
    return rows


def _write(path: Path, value: Any):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+".tmp")
    temporary.write_text(json.dumps(value,indent=2,sort_keys=True)+"\n"); temporary.replace(path)


def build_week(args, provider: EspnNflProvider, week: int) -> dict[str, Any]:
    wdir=snapshot_week_dir(args.snapshot_root,"nfl",args.season,week)
    if args.resume and all((wdir/f"{d}.json").exists() for d in FEATURE_DATASETS):
        return {"week":week,"status":"skipped","diagnostics":[]}
    games=provider.fetch_games(args.season,week)
    requested_game=getattr(args,"game_id",None)
    if requested_game:
        requested_game=str(requested_game)
        games=[game for game in games if requested_game in {str(game.get("game_id")),str(game.get("espn_event_id"))}]
        if not games: raise ValueError(f"requested game not found in cached scoreboard: {requested_game}")
    outcomes=normalize_outcomes(provider.fetch_outcomes(args.season,week,games),games,"nfl",args.season,week)
    diagnostics=[]; players=[]
    completed_ids={row.get("game_id") for row in outcomes}
    for game in games:
        if game.get("game_id") not in completed_ids: continue
        event=str(game.get("espn_event_id") or str(game.get("game_id","")).removeprefix("espn-"))
        try:
            payload=provider._summary(args.season,week,event)
            if not isinstance(payload,dict) or not isinstance(payload.get("boxscore"),dict):
                raise ValueError("missing object field: boxscore")
            completed=_completed_at(game)
            extraction={}
            normalized=normalize_espn_player_boxscore(payload,str(args.season),week,extraction)
            raw_ids={str(((ath.get("athlete") or {}).get("id"))) for team in payload["boxscore"].get("players",[])
                     for group in team.get("statistics",[]) for ath in group.get("athletes",[])
                     if (ath.get("athlete") or {}).get("id") is not None}
            emitted_ids={str(row.get("athlete_id")) for row in normalized if row.get("athlete_id") is not None}
            extraction.update({"week":week,"game_id":game["game_id"],"event_id":event,
                "classification":"ESPN_PLAYER_STATS_EXTRACTION",
                "emitted_ids_match_raw_athlete_ids":emitted_ids <= raw_ids,
                "evidence":[{"player_name":row.get("player_name"),"provider_player_id":row.get("provider_player_id"),
                    "category":row.get("category"),"stats":row.get("stats")} for row in normalized
                    if row.get("player_name") == "Dak Prescott" or str(row.get("provider_player_id")) == "2577417"]})
            diagnostics.append(extraction)
            for row in normalized:
                row.update({"league":"nfl","season":args.season,"week":week,"through_week":week,
                    "game_id":game["game_id"],"completed_at":completed,"captured_at":completed,
                    "data_as_of":completed,"record_role":"completed_game_history","is_pregame":False,"source":"espn"})
                players.append(row)
        except Exception as exc:
            diagnostics.append({"week":week,"game_id":game.get("game_id"),"event_id":event,
                                "classification":"MALFORMED_OR_UNAVAILABLE_ESPN_SUMMARY","message":str(exc)[:500]})
    datasets={"games":games,"player_stats":players,"team_stats":_team_rows(games,outcomes),
              "outcomes":outcomes,"injuries":provider.fetch_injuries(args.season,week,games)}
    for name,rows in datasets.items(): _write(wdir/f"{name}.json",rows)
    manifest={"schema_version":1,"builder":"nfl-feature-history-v1","provider":"espn","season":args.season,
        "week":week,"feature_only":True,"paid_requests_required":0,"estimated_paid_credits":0,
        "outcomes_role":"grading_only","datasets":{k:{"records":len(v),"sha256":hashlib.sha256((wdir/f'{k}.json').read_bytes()).hexdigest()} for k,v in datasets.items()},
        "diagnostics":diagnostics,"leakage_policy":"Only completed games strictly before a prediction cutoff are eligible."}
    _write(wdir/"manifest.json",manifest); _write(wdir/"metadata.json",manifest)
    return {"week":week,"status":"complete","datasets":{k:len(v) for k,v in datasets.items()},"diagnostics":diagnostics}


def main(argv=None):
    args=parse_args(argv)
    if args.start_week > args.end_week: raise SystemExit("--start-week must not exceed --end-week")
    cache=JsonRawCache(args.cache_root or args.snapshot_root.parent/"raw_cache")
    provider=EspnNflProvider(cache=cache,allow_network=args.allow_network and not args.rebuild_from_cache)
    plan=make_plan(args,provider)
    if args.plan or (not args.allow_network and not args.rebuild_from_cache):
        print(json.dumps(plan,indent=2,sort_keys=True))
        if not args.plan: print("Network disabled; rerun with --allow-network to build.")
        return 0
    reports=[]
    for week in range(args.start_week,args.end_week+1):
        try: reports.append(build_week(args,provider,week))
        except Exception as exc: reports.append({"week":week,"status":"failed","diagnostics":[{"message":str(exc)[:500]}]})
    report={"season":args.season,"weeks":reports,"cache_hits":cache.hits,"free_requests_required":cache.misses,
            "network_contacted":cache.misses>0,"paid_requests_required":0,"estimated_paid_credits":0}
    print(json.dumps(report,indent=2,sort_keys=True))
    if args.validate:
        invalid=[r for r in reports if r["status"]=="failed" or (r.get("status")=="complete" and not r["datasets"].get("player_stats"))
                 or any(d.get("classification")=="ESPN_PLAYER_STATS_EXTRACTION" and
                        (not d.get("emitted_ids_match_raw_athlete_ids") or
                         (d.get("raw_player_stat_records_inspected",0)>0 and not
                          sum(d.get(f"{c}_rows_emitted",0) for c in ("passing","rushing","receiving"))))
                        for d in r.get("diagnostics",[]))]
        if invalid: return 1
    return int(any(r["status"]=="failed" for r in reports))


if __name__ == "__main__": raise SystemExit(main())
