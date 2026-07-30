"""Offline development evaluation for the correlated NFL simulation engine."""
from __future__ import annotations

import argparse
import json
import os
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
from .team_history import (filter_market_quotes, prediction_cutoff,
                           prediction_cutoff_source)

MODELS = ("nfl_game_baseline_v1", "nfl_game_baseline_v2", "nfl_game_baseline_v3")


def evaluate(root: Path, season: int, start: int, end: int, model_version: str,
             simulations: int, seed: int) -> dict[str, Any]:
    if end >= 7:
        raise ValueError("development simulation evaluation may not expose holdout Week 7+")
    analytics = evaluate_analytic(root, season, start, end, list(MODELS), NFLV3Config(), "DEVELOPMENT RESULT")
    provider=HistoricalSnapshotProvider(root); simulator=NFLGameSimulator(); rows=[]; all_player_rows=[]
    canonical_players, canonical_audit = provider.canonical_player_history("nfl",str(season))
    rejection_histogram={key:0 for key in canonical_audit["rejections"]}
    modeled_examples=[]; correlation_values=[]
    exclusions=[]
    coverage={"games_evaluated":0,"team_rows_loaded":0,"team_rows_used":0,
              "player_rows_loaded":0,"player_rows_used":0,"future_rows_rejected":0,
              "player_rows_rejected_unknown_timestamp":0,"market_rows_loaded":0,
              "market_rows_used":0,"market_rows_rejected_future":0,
              "cutoff_sources":{"prediction_cutoff":0,"prediction_timestamp":0,"kickoff_fallback":0}}
    readiness={"READY":0,"NOT_READY_NO_PLAYER_DATA":0,"NOT_READY_INSUFFICIENT_HISTORY":0}
    started=perf_counter()
    for week in range(start,end+1):
        directory=snapshot_week_dir(root,"nfl",season,week)
        player_path=directory/"player_stats.json"
        weekly_player_rows=json.loads(player_path.read_text()) if player_path.exists() else []
        all_player_rows.extend(weekly_player_rows)
        games=provider.get_games("nfl",str(season),week)
        weekly_odds=provider.get_odds("nfl",str(season),week)
        outcomes={str(o.get("game_id")):o for o in provider.get_outcomes("nfl",str(season),week)}
        predictor=NFLGameMarketPredictor(model_version, NFLV3Config() if model_version==MODELS[2] else None)
        for index,game in enumerate(games):
            views = provider.get_game_histories("nfl",str(season),week,game)
            league_filter, team_filter, player_filter = views.league_team_history, views.target_team_history, views.player_history
            league_histories=league_filter.rows; histories=team_filter.rows; player_rows=player_filter.rows
            coverage["team_rows_loaded"] += league_filter.loaded
            coverage["team_rows_used"] += len(league_histories)
            coverage["player_rows_loaded"] += player_filter.loaded
            coverage["player_rows_used"] += len(player_rows)
            coverage["future_rows_rejected"] += league_filter.rejected_future + player_filter.rejected_future
            coverage["player_rows_rejected_unknown_timestamp"] += player_filter.rejected_unknown_timestamp
            for reason,count in player_filter.rejection_histogram.items(): rejection_histogram[reason]+=count
            cutoff=prediction_cutoff(game)
            source=prediction_cutoff_source(game)
            coverage["cutoff_sources"][source] = coverage["cutoff_sources"].get(source, 0) + 1
            game_odds=[row for row in weekly_odds if str(row.get("game_id")) == str(game.get("game_id"))]
            eligible_odds, market_filter=filter_market_quotes(game,game_odds)
            coverage["market_rows_loaded"] += market_filter["loaded"]
            coverage["market_rows_used"] += market_filter["eligible"]
            coverage["market_rows_rejected_future"] += market_filter["rejected_future"]
            history_diagnostic={
                "league_team_rows_loaded":league_filter.loaded,"league_team_rows_eligible":len(league_histories),
                "league_team_rows_rejected_future":league_filter.rejected_future,
                "target_team_rows_eligible":len(histories),
                "player_rows_loaded":player_filter.loaded,"player_rows_eligible":len(player_rows),
                "player_rows_rejected_future":player_filter.rejected_future,
                "player_rows_rejected_unknown_timestamp":player_filter.rejected_unknown_timestamp,
                "league_teams":sorted({str(r.get("team")) for r in league_histories}),
                "historical_seasons":sorted({str(r.get("season")) for r in league_histories}),
                "latest_team_history_timestamp":league_filter.latest_timestamp,
                "latest_player_history_timestamp":player_filter.latest_timestamp,
                "latest_market_snapshot_timestamp":market_filter["latest_timestamp"],
                "eligible_market_quote_ids":[str(q.get("quote_id") or q.get("id") or q.get("snapshot_timestamp") or q.get("captured_at")) for q in eligible_odds],
                "prediction_cutoff":cutoff.isoformat().replace("+00:00","Z") if cutoff else None,
                "cutoff_source":source}
            if os.getenv("BACKTESTING_DEBUG_HISTORY") == "1":
                for dataset, filtered in (("team_stats",team_filter),("player_stats",player_filter)):
                    for offender in filtered.rejected_rows:
                        detail={"target_game_id":game.get("game_id"),"target_week":week,
                            "target_kickoff":game.get("kickoff_time") or game.get("commence_time"),
                            "prediction_cutoff":history_diagnostic["prediction_cutoff"],"dataset_type":dataset,
                            "offending_row_game_id":offender.get("game_id"),"player":offender.get("player_name") or offender.get("player"),
                            "team":offender.get("team"),"row_season":offender.get("season"),"row_week":offender.get("week"),
                            "data_as_of":offender.get("data_as_of"),"completed_at":offender.get("completed_at"),
                            "captured_at":offender.get("captured_at"),"record_role":offender.get("record_role"),
                            "source":offender.get("source"),"rejection_reason":offender.get("rejection_reason")}
                        print("History rejection: "+json.dumps(detail,sort_keys=True,default=str))
            predictor_history = histories if model_version == MODELS[0] else league_histories
            projection=predictor.project(game,predictor_history, None) if model_version==MODELS[2] else predictor.project(game,predictor_history)
            outcome=outcomes.get(str(game.get("game_id")))
            if projection is None or not outcome or outcome.get("final_home_score") is None:
                reason="projection_unavailable" if projection is None else "outcome_unavailable"
                exclusions.append({"week":week,"game_id":str(game.get("game_id")),"reason":reason,
                                   "history_diagnostics":history_diagnostic})
                continue
            result=simulator.simulate(game,histories,player_rows,None,model_version,simulations,seed+week*1000+index,projection)
            ready_players=len({player for player, _market in result.player_outcomes})
            modeled_examples.extend(sorted({player for player,_ in result.player_outcomes})[:3])
            qbs=[key for key in result.player_outcomes if key[1]=="passing_yards"]
            receivers=[key for key in result.player_outcomes if key[1]=="receiving_yards"]
            if qbs and receivers:
                correlation_values.append(result.correlation(qbs[0],receivers[0])["pearson"])
            readiness["READY"] += ready_players
            if not ready_players:
                readiness["NOT_READY_NO_PLAYER_DATA" if not player_filter.loaded else "NOT_READY_INSUFFICIENT_HISTORY"] += 1
            actual_home=float(outcome["final_home_score"]); actual_away=float(outcome["final_away_score"])
            rows.append({"week":week,"game_id":str(game.get("game_id")),"home_win_probability":result.market_probability("moneyline")["home"],
                         "home_win":int(actual_home>actual_away),"simulated_home":float(result.home_points.mean()),
                         "simulated_away":float(result.away_points.mean()),"actual_home":actual_home,"actual_away":actual_away,
                         "history_diagnostics":history_diagnostic,
                         "player_prop_readiness":"READY" if ready_players else ("NOT_READY_NO_PLAYER_DATA" if not player_filter.loaded else "NOT_READY_INSUFFICIENT_HISTORY")})
            coverage["games_evaluated"] += 1
    elapsed=perf_counter()-started
    probs=probability_metrics((r["home_win_probability"],r["home_win"]) for r in rows)
    mae=lambda values: sum(abs(a-b) for a,b in values)/len(rows) if rows else None
    simulation_summary={"games":len(rows),"moneyline":probs,
        "margin_mae":mae(((r["simulated_home"]-r["simulated_away"],r["actual_home"]-r["actual_away"]) for r in rows)),
        "total_mae":mae(((r["simulated_home"]+r["simulated_away"],r["actual_home"]+r["actual_away"]) for r in rows))}
    return {"title":"NFL Simulation Development Evaluation","season":season,"weeks":[start,end],
            "configuration":{"model_version":model_version,"simulation_version":"nfl-game-simulation-v1","simulations_per_game":simulations,"base_seed":seed},
            "dataset_readiness":audit_player_stats(canonical_players),"player_schema_audit":canonical_audit,
            "team_score_simulation":simulation_summary,
            "history_coverage":coverage,"game_exclusions":exclusions,
            "analytic_comparison":analytics["models"],"player_prop_metrics":{"readiness_counts":readiness,
                "players_modeled":len(set(modeled_examples)),"examples":sorted(set(modeled_examples))[:12]},
            "player_history":{"provider_rows_discovered":canonical_audit["provider_rows"],
                "canonical_player_game_observations":len(canonical_players),
                "eligible_historical_observations":coverage["player_rows_used"],
                "rejection_histogram":rejection_histogram},
            "correlations":{"qb_passing_to_receiver_yards_mean":sum(correlation_values)/len(correlation_values) if correlation_values else None,
                "observations":len(correlation_values),"causal_claim":False},
            "runtime":{"seconds":elapsed,"games":len(rows),"simulations":len(rows)*simulations},
            "readiness":{"team_markets":"READY","player_props":"READY_WHERE_HISTORY_ELIGIBLE",
                "historical_sgp":"NOT_READY_NO_HISTORICAL_PLAYER_PRICES","anytime_td":"NOT READY"},
            "known_limitations":["No historical injury/active-status source; availability confidence is explicit.",
                "Player prop backtests require canonical postgame player outcomes and historical prop lines.",
                "The initial score distribution requires calibration on a larger development sample."],"rows":rows}


def markdown(report: dict[str,Any]) -> str:
    readiness=report["readiness"]; sim=report["team_score_simulation"]
    sections=["# NFL Simulation Development Evaluation","## Dataset readiness",f"Player-game rows: {report['dataset_readiness']['rows']}",
      "## Leakage-safe history coverage",json.dumps(report["history_coverage"],sort_keys=True),
      "## Team-simulation exclusions",json.dumps(report["game_exclusions"],sort_keys=True),
      "## Team score simulation",f"Games: {sim['games']}; margin MAE: {sim['margin_mae']}; total MAE: {sim['total_mae']}",
      "## Moneyline probabilities",json.dumps(sim["moneyline"],sort_keys=True),"## Spread probabilities","Simulation API supports cover/win/push counts for arbitrary lines.",
      "## Total probabilities","Simulation API supports over/under/push counts for arbitrary lines.","## Player prop coverage",json.dumps(report["dataset_readiness"]["markets"],sort_keys=True),
      "Readiness: "+json.dumps(report["player_prop_metrics"]["readiness_counts"],sort_keys=True),
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
