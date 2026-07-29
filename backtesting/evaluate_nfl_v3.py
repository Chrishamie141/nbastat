"""Offline-only NFL V3 development and explicitly unlocked holdout evaluation."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from typing import Any

from .evaluation import error_metrics, probability_metrics
from .nfl_game_predictor import NFLGameMarketPredictor, V1_MODEL_VERSION, V2_MODEL_VERSION
from .nfl_v3 import (NFLResearchSplit, NFLV3Config, V3_MODEL_VERSION, chronological_folds,
                     create_holdout_manifest, verify_holdout_manifest)


def _load(path: Path) -> list[dict[str, Any]]:
    try:
        value=json.loads(path.read_text()); return value if isinstance(value,list) else []
    except (OSError,json.JSONDecodeError): return []


def snapshot_hashes(root: Path, season: int, start: int, end: int) -> dict[str,str]:
    result={}
    for week in range(start,end+1):
        directory=root/"nfl"/str(season)/f"week_{week}"
        for path in sorted(directory.glob("*.json")):
            result[str(path.relative_to(root))]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def evaluate(root: Path, season: int, start: int, end: int, models: list[str], config: NFLV3Config,
             label: str) -> dict[str,Any]:
    rows={model:[] for model in models}
    for week in range(start,end+1):
        d=root/"nfl"/str(season)/f"week_{week}"; games=_load(d/"games.json"); history=_load(d/"team_stats.json"); outcomes=_load(d/"outcomes.json"); odds=_load(d/"odds.json")
        outcome_map={str(o.get("game_id")):o for o in outcomes}; market_map={str(o.get("game_id")):o for o in odds if o.get("game_id")}
        for game in games:
            outcome=outcome_map.get(str(game.get("game_id")))
            if not outcome or outcome.get("final_home_score") is None or outcome.get("final_away_score") is None: continue
            for model in models:
                predictor=NFLGameMarketPredictor(model,config if model==V3_MODEL_VERSION else None)
                projection=predictor.project(game,history,market_map.get(str(game.get("game_id")))) if model==V3_MODEL_VERSION else predictor.project(game,history)
                if not projection: continue
                ah=float(outcome["final_home_score"]); aa=float(outcome["final_away_score"])
                rows[model].append({"week":week,"game_id":game.get("game_id"),"probability":projection.probability("h2h",game["home_team"],home_team=game["home_team"],away_team=game["away_team"]),"outcome":int(ah>aa),"projected_home":projection.home_points,"projected_away":projection.away_points,"actual_home":ah,"actual_away":aa,"feature_diagnostics":getattr(predictor,"last_feature_diagnostics",{})})
    summaries={}
    for model,items in rows.items():
        prob=probability_metrics((r["probability"],r["outcome"]) for r in items)
        summaries[model]={"games":len(items),"probability":prob,
          "home_error":error_metrics((r["projected_home"],r["actual_home"]) for r in items),
          "away_error":error_metrics((r["projected_away"],r["actual_away"]) for r in items),
          "margin_error":error_metrics((r["projected_home"]-r["projected_away"],r["actual_home"]-r["actual_away"]) for r in items),
          "total_error":error_metrics((r["projected_home"]+r["projected_away"],r["actual_home"]+r["actual_away"]) for r in items),
          "weekly":{str(w):len([r for r in items if r["week"]==w]) for w in range(start,end+1)}}
    return {"result_type":label,"season":season,"evaluation_window":{"start_week":start,"end_week":end},"models":summaries,"configuration":asdict(config),"configuration_hash":config.configuration_hash,"chronological_folds":chronological_folds(range(start,end+1)),"optimization_objectives":["brier","log_loss","margin_mae","total_mae"],"roi_role":"secondary_diagnostic_only"}


def markdown(report: dict[str,Any]) -> str:
    lines=[f"# {report['result_type']}","",f"Evaluation window: Weeks {report['evaluation_window']['start_week']}–{report['evaluation_window']['end_week']}","", "| Model | Games | Brier | Log loss | Margin MAE | Total MAE |","|---|---:|---:|---:|---:|---:|"]
    for model,s in report["models"].items(): lines.append(f"| {model} | {s['games']} | {s['probability'].get('brier')} | {s['probability'].get('log_loss')} | {s['margin_error'].get('mae')} | {s['total_error'].get('mae')} |")
    return "\n".join(lines)+"\n"


def main(argv: list[str]|None=None) -> int:
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--snapshot-root",type=Path,default=Path("backtesting/snapshots")); p.add_argument("--season",type=int,required=True)
    p.add_argument("--development-start-week",type=int,default=1); p.add_argument("--development-end-week",type=int,default=6); p.add_argument("--holdout-start-week",type=int,default=7); p.add_argument("--holdout-end-week",type=int,default=18)
    p.add_argument("--models",default=f"{V1_MODEL_VERSION},{V2_MODEL_VERSION},{V3_MODEL_VERSION}"); p.add_argument("--output",type=Path); p.add_argument("--markdown",type=Path); p.add_argument("--config",type=Path)
    p.add_argument("--freeze-holdout",action="store_true"); p.add_argument("--evaluate-holdout",action="store_true"); p.add_argument("--frozen-config",type=Path)
    args=p.parse_args(argv); config=NFLV3Config(**json.loads(args.config.read_text())) if args.config else NFLV3Config(); split=NFLResearchSplit(args.development_start_week,args.development_end_week,args.holdout_start_week)
    if args.freeze_holdout:
        if not args.frozen_config: p.error("--freeze-holdout requires --frozen-config manifest path")
        hashes=snapshot_hashes(args.snapshot_root,args.season,args.holdout_start_week,args.holdout_end_week); create_holdout_manifest(args.frozen_config,args.season,split,config,hashes); return 0
    if args.evaluate_holdout:
        if not args.frozen_config: p.error("--evaluate-holdout requires --frozen-config")
        hashes=snapshot_hashes(args.snapshot_root,args.season,args.holdout_start_week,args.holdout_end_week); verify_holdout_manifest(args.frozen_config,config,hashes)
        start,end,label=args.holdout_start_week,args.holdout_end_week,"HOLDOUT RESULT"
    else: start,end,label=args.development_start_week,args.development_end_week,"DEVELOPMENT RESULT"
    report=evaluate(args.snapshot_root,args.season,start,end,args.models.split(","),config,label)
    if args.output: args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    if args.markdown: args.markdown.parent.mkdir(parents=True,exist_ok=True); args.markdown.write_text(markdown(report))
    if not args.output: print(json.dumps(report,indent=2,sort_keys=True))
    return 0

if __name__ == "__main__": raise SystemExit(main())
