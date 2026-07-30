"""Offline development evaluation for the correlated NFL simulation engine."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter
from typing import Any

from .config import SNAPSHOTS_DIR
from .evaluate_nfl_v3 import evaluate as evaluate_analytic
from .historical_provider import HistoricalSnapshotProvider
from .evaluation import probability_metrics
from .nfl_game_predictor import NFLGameMarketPredictor
from .nfl_simulation import NFLGameSimulator, audit_player_stats
from .nfl_v3 import NFLV3Config
from .snapshots import snapshot_week_dir

MODELS = ("nfl_game_baseline_v1", "nfl_game_baseline_v2", "nfl_game_baseline_v3")


def evaluate(root: Path, season: int, start: int, end: int, model_version: str,
             simulations: int, seed: int) -> dict[str, Any]:
    if end >= 7:
        raise ValueError("development simulation evaluation may not expose holdout Week 7+")
    analytics = evaluate_analytic(root, season, start, end, list(MODELS), NFLV3Config(), "DEVELOPMENT RESULT")
    provider=HistoricalSnapshotProvider(root); simulator=NFLGameSimulator(); rows=[]; all_player_rows=[]
    started=perf_counter()
    for week in range(start,end+1):
        directory=snapshot_week_dir(root,"nfl",season,week)
        player_path=directory/"player_stats.json"
        player_rows=json.loads(player_path.read_text()) if player_path.exists() else []
        all_player_rows.extend(player_rows)
        histories=provider.get_team_stats("nfl",str(season),week)
        games=provider.get_games("nfl",str(season),week)
        outcomes={str(o.get("game_id")):o for o in provider.get_outcomes("nfl",str(season),week)}
        predictor=NFLGameMarketPredictor(model_version, NFLV3Config() if model_version==MODELS[2] else None)
        for index,game in enumerate(games):
            projection=predictor.project(game,histories, None) if model_version==MODELS[2] else predictor.project(game,histories)
            outcome=outcomes.get(str(game.get("game_id")))
            if projection is None or not outcome or outcome.get("final_home_score") is None: continue
            result=simulator.simulate(game,histories,player_rows,None,model_version,simulations,seed+week*1000+index,projection)
            actual_home=float(outcome["final_home_score"]); actual_away=float(outcome["final_away_score"])
            rows.append({"week":week,"game_id":str(game.get("game_id")),"home_win_probability":result.market_probability("moneyline")["home"],
                         "home_win":int(actual_home>actual_away),"simulated_home":float(result.home_points.mean()),
                         "simulated_away":float(result.away_points.mean()),"actual_home":actual_home,"actual_away":actual_away})
    elapsed=perf_counter()-started
    probs=probability_metrics((r["home_win_probability"],r["home_win"]) for r in rows)
    mae=lambda values: sum(abs(a-b) for a,b in values)/len(rows) if rows else None
    simulation_summary={"games":len(rows),"moneyline":probs,
        "margin_mae":mae(((r["simulated_home"]-r["simulated_away"],r["actual_home"]-r["actual_away"]) for r in rows)),
        "total_mae":mae(((r["simulated_home"]+r["simulated_away"],r["actual_home"]+r["actual_away"]) for r in rows))}
    return {"title":"NFL Simulation Development Evaluation","season":season,"weeks":[start,end],
            "configuration":{"model_version":model_version,"simulation_version":"nfl-game-simulation-v1","simulations_per_game":simulations,"base_seed":seed},
            "dataset_readiness":audit_player_stats(all_player_rows),"team_score_simulation":simulation_summary,
            "analytic_comparison":analytics["models"],"player_prop_metrics":{},
            "correlations":{"status":"per-game diagnostics available on SimulationResult","causal_claim":False},
            "runtime":{"seconds":elapsed,"games":len(rows),"simulations":len(rows)*simulations},
            "readiness":{"team_markets":"READY","player_props":"PARTIAL","historical_sgp":"PARTIAL","anytime_td":"NOT READY"},
            "known_limitations":["No historical injury/active-status source; availability confidence is explicit.",
                "Player prop backtests require canonical postgame player outcomes and historical prop lines.",
                "The initial score distribution requires calibration on a larger development sample."],"rows":rows}


def markdown(report: dict[str,Any]) -> str:
    readiness=report["readiness"]; sim=report["team_score_simulation"]
    sections=["# NFL Simulation Development Evaluation","## Dataset readiness",f"Player-game rows: {report['dataset_readiness']['rows']}",
      "## Team score simulation",f"Games: {sim['games']}; margin MAE: {sim['margin_mae']}; total MAE: {sim['total_mae']}",
      "## Moneyline probabilities",json.dumps(sim["moneyline"],sort_keys=True),"## Spread probabilities","Simulation API supports cover/win/push counts for arbitrary lines.",
      "## Total probabilities","Simulation API supports over/under/push counts for arbitrary lines.","## Player prop coverage",json.dumps(report["dataset_readiness"]["markets"],sort_keys=True),
      "## QB passing","PARTIAL: passing yards and passing TD distributions.","## RB rushing","PARTIAL: attempts and yards where history supports them.",
      "## Receiving","PARTIAL: receptions and receiving yards.","## Calibration","Simulation results are retained beside V1/V2/V3 analytic metrics.",
      "## Simulation correlations","Empirical Pearson diagnostics are available; they are not causal claims.","## Runtime",json.dumps(report["runtime"],sort_keys=True),
      "## Known limitations","\n".join(f"- {x}" for x in report["known_limitations"]),"## SGP readiness",
      f"Team markets: **{readiness['team_markets']}**; player props: **{readiness['player_props']}**; historical SGP: **{readiness['historical_sgp']}**; anytime TD: **{readiness['anytime_td']}**."]
    return "\n\n".join(sections)+"\n"


def main(argv: list[str] | None=None) -> int:
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--snapshot-root",type=Path,default=SNAPSHOTS_DIR)
    p.add_argument("--season",type=int,required=True); p.add_argument("--start-week",type=int,default=1); p.add_argument("--end-week",type=int,default=6)
    p.add_argument("--model-version",choices=MODELS,default=MODELS[2]); p.add_argument("--simulations",type=int,default=10000); p.add_argument("--seed",type=int,default=141)
    p.add_argument("--output",type=Path); p.add_argument("--markdown",type=Path); args=p.parse_args(argv)
    report=evaluate(args.snapshot_root,args.season,args.start_week,args.end_week,args.model_version,args.simulations,args.seed)
    if args.output: args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    if args.markdown: args.markdown.parent.mkdir(parents=True,exist_ok=True); args.markdown.write_text(markdown(report))
    if not args.output: print(json.dumps(report,indent=2,sort_keys=True))
    return 0

if __name__ == "__main__": raise SystemExit(main())
