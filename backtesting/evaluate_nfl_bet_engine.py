"""Offline-only historical NFL ticket-engine evaluation CLI."""

from __future__ import annotations

import argparse, csv, json
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import SNAPSHOTS_DIR
from .historical_provider import HistoricalSnapshotProvider
from .markets import normalize_market
from .nfl_bet_engine import (RiskProfile, TicketEngine, aggregate_rejections,
                             grade_ticket, normalize_candidate, parlay_reliability)
from .nfl_game_predictor import NFLGameMarketPredictor


def _time(value: Any) -> datetime | None:
    try:
        stamp=datetime.fromisoformat(str(value).replace("Z","+00:00"))
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    except (TypeError,ValueError): return None


def generate_candidates(provider: HistoricalSnapshotProvider, season: str, week: int, model: str):
    """Create priceable candidates solely from pre-kickoff immutable inputs."""
    games=provider.get_games("nfl",season,week); odds=provider.get_odds("nfl",season,week)
    history=provider.get_team_stats("nfl",season,week); predictor=NFLGameMarketPredictor(model); output=[]; rejected=[]
    by_game=defaultdict(list)
    for quote in odds: by_game[str(quote.get("game_id"))].append(quote)
    for game in games:
        gid=str(game.get("game_id")); kickoff=_time(game.get("kickoff_time")); projection=predictor.project(game,history)
        if projection is None: rejected.append({"game_id":gid,"reason":"insufficient_pregame_history"}); continue
        # Keep the best executable quote per economic selection.
        best={}
        for quote in by_game[gid]:
            market=normalize_market(quote.get("market")); captured=_time(quote.get("captured_at") or quote.get("snapshot_timestamp") or quote.get("data_as_of"))
            if market not in {"h2h","spread","total"} or not captured or not kickoff or captured>=kickoff: rejected.append({"game_id":gid,"reason":"post_kickoff_or_invalid_quote"}); continue
            try:
                american=float(quote["odds"]); implied=100/(american+100) if american>0 else abs(american)/(abs(american)+100)
                probability=projection.probability(market,str(quote["selection"]),quote.get("line"),home_team=str(game["home_team"]),away_team=str(game["away_team"]))
            except (KeyError,TypeError,ValueError): rejected.append({"game_id":gid,"reason":"invalid_quote"}); continue
            row={**quote,**game,"model_name":model,"market":market,"american_odds":american,"implied_probability":implied,
                 "model_probability":probability,"edge":probability-implied,"expected_value":probability*(1+(american/100 if american>0 else 100/abs(american)))-1,
                 "snapshot_timestamp":quote.get("snapshot_timestamp") or quote.get("captured_at"),"data_as_of":projection.data_as_of,
                 "projected_home_score":projection.home_points,"projected_away_score":projection.away_points}
            key=(market,str(quote.get("selection")).casefold(),quote.get("line"))
            candidate=normalize_candidate(row)
            if key not in best or candidate.expected_value>best[key].expected_value: best[key]=candidate
        output.extend(best.values())
    return sorted(output,key=lambda c:(c.game_id,c.market,c.selection,c.line or 0,c.candidate_id)),rejected


def _metrics(tickets, opportunities: int, no_bets: int) -> dict[str,Any]:
    grades=Counter(t.historical_grade for t in tickets); wagered=sum(t.stake for t in tickets); profit=sum(t.profit or 0 for t in tickets)
    legs=[leg for t in tickets for leg in t.legs]; valid_joint=[t.estimated_joint_probability for t in tickets if t.estimated_joint_probability is not None]
    return {"opportunities":opportunities,"tickets_generated":len(tickets),"no_bet_slates":no_bets,"total_legs":len(legs),
            "average_legs_per_ticket":len(legs)/len(tickets) if tickets else None,"tickets_won":grades["win"],"tickets_lost":grades["loss"],
            "tickets_pushed":grades["push"],"tickets_ungraded":grades["ungraded"],"ticket_hit_rate":grades["win"]/(grades["win"]+grades["loss"]) if grades["win"]+grades["loss"] else None,
            "flat_stake_wagered":wagered,"profit_loss":profit,"roi":profit/wagered if wagered else None,
            "average_sportsbook_payout":sum(t.combined_decimal_odds for t in tickets)/len(tickets) if tickets else None,
            "average_estimated_joint_probability":sum(valid_joint)/len(valid_joint) if valid_joint else None,
            "average_estimated_ev":sum(t.estimated_ticket_ev for t in tickets if t.estimated_ticket_ev is not None)/sum(t.estimated_ticket_ev is not None for t in tickets) if any(t.estimated_ticket_ev is not None for t in tickets) else None,
            "leg_level_win_rate":sum(l.final_result=="win" for l in legs)/sum(l.final_result in {"win","loss"} for l in legs) if any(l.final_result in {"win","loss"} for l in legs) else None}


def evaluate(season: str, start_week: int, end_week: int, models: list[str], ticket_types: list[str], profiles: list[RiskProfile], stake: float, data_dir: Path=SNAPSHOTS_DIR, debug_rejections: bool=False):
    provider=HistoricalSnapshotProvider(data_dir); engine=TicketEngine(); all_tickets=[]; rejections=[]; sgp_diagnostics=[]; strategy=defaultdict(lambda:{"tickets":[],"opportunities":0,"no_bets":0})
    for week in range(start_week,end_week+1):
        outcomes={str(x.get("game_id")):x for x in provider.get_outcomes("nfl",season,week)}
        for model in models:
            candidates,source_rejections=generate_candidates(provider,season,week,model); rejections.extend({**r,"week":week,"model":model} for r in source_rejections)
            for profile in profiles:
                builders={"singles":lambda:engine.singles(candidates,profile,stake),"winner":lambda:engine.winner_parlay(candidates,profile,stake),
                          "sgp":lambda:engine.same_game_parlays(candidates,profile,stake),"slate":lambda:engine.slate_parlay(candidates,profile,stake)}
                for kind in ticket_types:
                    key=f"{model}:{kind}:{profile.value}"; strategy[key]["opportunities"]+=1; built=builders[kind]()
                    if built.no_bet: strategy[key]["no_bets"]+=1
                    if kind=="sgp": sgp_diagnostics.append({"model":model,"week":week,"risk_profile":profile.value,**built.diagnostics})
                    rejections.extend({**asdict(r),"week":week,"model":model,"strategy":kind,"risk_profile":profile.value} for r in built.rejections)
                    for ticket in built.tickets:
                        # Grade a copy of every leg for candidate comparison/audit.
                        grade_ticket(ticket,outcomes)
                        for leg in ticket.legs:
                            result=__import__("backtesting.grader",fromlist=["PredictionGrader"]).PredictionGrader().grade({"game_id":leg.game_id,"market":leg.market,"selection":leg.selection,"line":leg.line},outcomes.get(leg.game_id))["grade"]
                            object.__setattr__(leg,"final_result",result)
                        strategy[key]["tickets"].append(ticket); all_tickets.append(ticket)
    metrics={key:_metrics(value["tickets"],value["opportunities"],value["no_bets"]) for key,value in sorted(strategy.items())}
    weekly=defaultdict(lambda:Counter())
    for t in all_tickets:
        week=t.legs[0].week; weekly[str(week)][t.historical_grade]+=1; weekly[str(week)]["profit"]+=t.profit or 0
    rejection_summary=aggregate_rejections(rejections,debug=debug_rejections)
    ordering={model:{"safe_gt_balanced":None,"balanced_gt_aggressive":None} for model in models}
    for model in models:
        values={p:metrics.get(f"{model}:winner:{p}",{}).get("average_estimated_joint_probability") for p in ("safe","balanced","aggressive")}
        if values["safe"] is not None and values["balanced"] is not None: ordering[model]["safe_gt_balanced"]=values["safe"]>values["balanced"]
        if values["balanced"] is not None and values["aggressive"] is not None: ordering[model]["balanced_gt_aggressive"]=values["balanced"]>values["aggressive"]
    return {"schema_version":2,"dataset":{"league":"nfl","season":season,"start_week":start_week,"end_week":end_week,"offline_only":True},
            "models":models,"strategy_metrics":metrics,"weekly_performance":{k:dict(v) for k,v in sorted(weekly.items())},
            "tickets":[t.as_dict() for t in all_tickets],"rejections":rejections if debug_rejections else [],"rejection_summary":rejection_summary,
            "risk_ordering":ordering,"parlay_reliability":parlay_reliability(all_tickets),"sgp_diagnostics":sgp_diagnostics,
            "candidate_comparison":{"ticket_legs":sum(len(t.legs) for t in all_tickets),"note":"Ticket ROI is a variance-sensitive construction metric; it is separate from prediction quality."}}


SECTIONS=("Dataset readiness","Singles","Winner Parlays","Same Game Parlay Funnel","SGP Market Availability","SGP Recommendations","SGP Rejection Reasons","Slate Parlays","SAFE vs BALANCED vs AGGRESSIVE","V1 vs V2","Weekly performance","Exposure diagnostics","Rejection/no-bet diagnostics","Ticket examples","Key weaknesses","Recommended next research")
def render_markdown(result):
    lines=["# NFL Betting Engine Evaluation","","> Prediction quality and ticket-construction quality are reported separately. Structural SGP scores are not probabilities.",""]
    diagnostics=result.get("sgp_diagnostics",[])
    for section in SECTIONS:
        lines += [f"## {section}"]
        if section=="Dataset readiness": lines += [f"Offline immutable snapshots: **yes**. Season {result['dataset']['season']}, Weeks {result['dataset']['start_week']}–{result['dataset']['end_week']}."]
        elif section in {"Singles","Winner Parlays","Slate Parlays"}:
            token={"Singles":":singles:","Winner Parlays":":winner:","Slate Parlays":":slate:"}[section]
            lines += ["```json",json.dumps({k:v for k,v in result.get("strategy_metrics",{}).items() if token in k},indent=2,sort_keys=True),"```"]
        elif section=="Same Game Parlay Funnel":
            for row in diagnostics:
                funnel=row["funnel"]; lines += [f"### {row['model']} — Week {row['week']} — {row['risk_profile'].upper()}","```json",json.dumps(funnel,indent=2,sort_keys=True),"```"]
                if not funnel.get("sgp_tickets_generated"):
                    stages=[(k,v) for k,v in funnel.items() if k.startswith("games_rejected_") and v]
                    dominant=max(stages,key=lambda x:(x[1],x[0]),default=("no_coherent_or_confident_script",funnel.get("no_bet_games",0)))
                    lines += [f"**Zero SGPs:** dominant terminal stage: `{dominant[0]}` ({dominant[1]} games)."]
        elif section=="SGP Market Availability":
            summary=Counter()
            for row in diagnostics:
                for game in row.get("market_availability",[]):
                    for market in ("moneyline","spread","total"): summary[market]+=bool(game.get(market))
                    summary["player_props"]+=bool(game.get("player_props"))
            lines += ["Historical snapshots only; unavailable markets are not synthesized.","```json",json.dumps(dict(sorted(summary.items())),indent=2),"```"]
        elif section=="SGP Recommendations":
            tickets=[t for t in result.get("tickets",[]) if t.get("ticket_type")=="same_game_parlay"]
            scripts=Counter(t.get("game_script") for t in tickets); legs=sum(t.get("number_of_legs",0) for t in tickets)
            no_bets=sum(d.get("funnel",{}).get("no_bet_games",0) for d in diagnostics)
            lines += [f"Tickets: **{len(tickets)}**; NO-BET games: **{no_bets}**; average legs: **{legs/len(tickets):.2f}**." if tickets else f"Tickets: **0**; NO-BET games: **{no_bets}**; average legs: **n/a**.",f"Game-script distribution: `{json.dumps(dict(sorted(scripts.items())))}`","```json",json.dumps(tickets[:5],indent=2,sort_keys=True),"```"]
        elif section=="SGP Rejection Reasons":
            examples=[{"model":d["model"],"week":d["week"],"risk_profile":d["risk_profile"],**x} for d in diagnostics for x in d.get("representative_rejections",[])]
            lines += ["```json",json.dumps(examples[:20],indent=2,sort_keys=True),"```"]
        elif section=="Weekly performance": lines += ["```json",json.dumps(result.get("weekly_performance",{}),indent=2,sort_keys=True),"```"]
        elif section=="Rejection/no-bet diagnostics": lines += ["```json",json.dumps(result.get("rejection_summary",{}),indent=2,sort_keys=True),"```"]
        elif section=="Ticket examples": lines += ["```json",json.dumps(result.get("tickets",[])[:2],indent=2,sort_keys=True),"```"]
        elif section=="Key weaknesses": lines += ["- SGP joint probability and EV remain unavailable without a correlation-aware estimator.","- A short replay is descriptive, not evidence of superiority."]
        elif section=="Recommended next research": lines += ["- Add a correlation-aware estimator trained only on data preceding each replay week."]
        else: lines += ["See the deterministic strategy metrics in the JSON artifact."]
        lines.append("")
    return "\n".join(lines)


def write_artifacts(result, output: Path, tickets: Path, markdown: Path):
    for path in (output,tickets,markdown): path.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n"); markdown.write_text(render_markdown(result)+"\n")
    rows=[]
    for ticket in result["tickets"]:
        row={k:v for k,v in ticket.items() if k!="legs"}; row["legs"]=json.dumps(ticket["legs"],sort_keys=True); rows.append(row)
    with tickets.open("w",newline="") as handle:
        fields=sorted({k for row in rows for k in row}) or ["ticket_id"]; writer=csv.DictWriter(handle,fieldnames=fields); writer.writeheader(); writer.writerows(rows)


def main():
    parser=argparse.ArgumentParser(description="Offline deterministic NFL ticket replay")
    parser.add_argument("--season",default="2025"); parser.add_argument("--start-week",type=int,default=1); parser.add_argument("--end-week",type=int,default=6)
    parser.add_argument("--models",default="nfl_game_baseline_v1,nfl_game_baseline_v2"); parser.add_argument("--ticket-types",default="singles,winner,sgp,slate")
    parser.add_argument("--debug-rejections",action="store_true",help="Include every rejection record instead of only counts/examples")
    parser.add_argument("--risk-profiles",default="safe,balanced,aggressive"); parser.add_argument("--stake",type=float,default=10); parser.add_argument("--data-dir",type=Path,default=SNAPSHOTS_DIR)
    parser.add_argument("--output",type=Path,required=True); parser.add_argument("--tickets",type=Path,required=True); parser.add_argument("--markdown",type=Path,required=True); args=parser.parse_args()
    result=evaluate(args.season,args.start_week,args.end_week,args.models.split(","),args.ticket_types.split(","),[RiskProfile(x) for x in args.risk_profiles.split(",")],args.stake,args.data_dir,args.debug_rejections)
    write_artifacts(result,args.output,args.tickets,args.markdown); print(f"Wrote {len(result['tickets'])} tickets (offline only)")

if __name__=="__main__": main()
