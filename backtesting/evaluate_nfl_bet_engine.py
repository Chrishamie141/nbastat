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
from .nfl_bet_engine import RiskProfile, TicketEngine, grade_ticket, normalize_candidate
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
                 "snapshot_timestamp":quote.get("snapshot_timestamp") or quote.get("captured_at"),"data_as_of":projection.data_as_of}
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


def evaluate(season: str, start_week: int, end_week: int, models: list[str], ticket_types: list[str], profiles: list[RiskProfile], stake: float, data_dir: Path=SNAPSHOTS_DIR):
    provider=HistoricalSnapshotProvider(data_dir); engine=TicketEngine(); all_tickets=[]; rejections=[]; strategy=defaultdict(lambda:{"tickets":[],"opportunities":0,"no_bets":0})
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
    return {"schema_version":1,"dataset":{"league":"nfl","season":season,"start_week":start_week,"end_week":end_week,"offline_only":True},
            "models":models,"strategy_metrics":metrics,"weekly_performance":{k:dict(v) for k,v in sorted(weekly.items())},
            "tickets":[t.as_dict() for t in all_tickets],"rejections":rejections,
            "candidate_comparison":{"ticket_legs":sum(len(t.legs) for t in all_tickets),"note":"Ticket ROI is a variance-sensitive construction metric; it is separate from prediction quality."}}


SECTIONS=("Dataset readiness","Singles","Winner Parlays","Same Game Parlays","Slate Parlays","SAFE vs BALANCED vs AGGRESSIVE","V1 vs V2","Weekly performance","Exposure diagnostics","Rejection/no-bet diagnostics","Ticket examples","Key weaknesses","Recommended next research")
def render_markdown(result):
    lines=["# NFL Betting Engine Evaluation","","> Prediction quality and ticket-construction quality are reported separately. This small sample does not establish strategy superiority.",""]
    for section in SECTIONS:
        lines += [f"## {section}"]
        if section=="Dataset readiness": lines += [f"Offline immutable snapshots: **yes**. Season {result['dataset']['season']}, Weeks {result['dataset']['start_week']}–{result['dataset']['end_week']}."]
        elif section in {"Singles","Winner Parlays","Same Game Parlays","Slate Parlays"}: lines += ["```json",json.dumps({k:v for k,v in result["strategy_metrics"].items() if section.split()[0].lower() in k.replace("winner","winner").replace("same","sgp")},indent=2,sort_keys=True),"```"]
        elif section=="Weekly performance": lines += ["```json",json.dumps(result["weekly_performance"],indent=2,sort_keys=True),"```"]
        elif section=="Rejection/no-bet diagnostics": lines += [f"Machine-readable rejection records: **{len(result['rejections'])}**."]
        elif section=="Ticket examples": lines += ["```json",json.dumps(result["tickets"][:2],indent=2,sort_keys=True),"```"]
        elif section=="Key weaknesses": lines += ["- SGP joint probability and EV remain unavailable without a correlation-aware estimator.","- A short replay is descriptive, not evidence of superiority."]
        elif section=="Recommended next research": lines += ["- Add simulation-backed SGP correlation estimates and evaluate on a held-out season."]
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
    parser.add_argument("--risk-profiles",default="safe,balanced,aggressive"); parser.add_argument("--stake",type=float,default=10); parser.add_argument("--data-dir",type=Path,default=SNAPSHOTS_DIR)
    parser.add_argument("--output",type=Path,required=True); parser.add_argument("--tickets",type=Path,required=True); parser.add_argument("--markdown",type=Path,required=True); args=parser.parse_args()
    result=evaluate(args.season,args.start_week,args.end_week,args.models.split(","),args.ticket_types.split(","),[RiskProfile(x) for x in args.risk_profiles.split(",")],args.stake,args.data_dir)
    write_artifacts(result,args.output,args.tickets,args.markdown); print(f"Wrote {len(result['tickets'])} tickets (offline only)")

if __name__=="__main__": main()
