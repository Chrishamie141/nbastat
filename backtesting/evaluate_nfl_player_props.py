"""Offline, leakage-safe evaluation of frozen NFL player-prop probabilities.

The command only reads snapshot JSON.  Model probabilities must already be
frozen in ``player_prop_odds.json`` or ``player_prop_predictions.json``; this
keeps evaluation separate from (and unable to alter) model construction.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from .config import SNAPSHOTS_DIR
from .game_matching import parse_dt
from .markets import CANONICAL_PLAYER_PROP_MARKETS
from .player_identity import canonical_player_key, first_player_id, normalize_player_id
from .player_prop_odds import aggregate_player_outcomes, decimal_from_american, grade_quote
from .snapshots import snapshot_week_dir
from .team_history import prediction_cutoff

EDGE_BUCKETS = ((-math.inf, 0.0, "edge <= 0"), (0.0, .02, "0-2%"),
                (.02, .05, "2-5%"), (.05, .10, "5-10%"),
                (.10, math.inf, ">=10%"))
PROBABILITY_BINS = tuple((i / 10, (i + 1) / 10, f"{i*10}-{(i+1)*10}%") for i in range(10))
MINIMUM_SAMPLE = 30
CLV_REASON = "No verified later pregame closing snapshot is part of the canonical player-prop schema."


def american_implied_probability(odds: int | float) -> float:
    """Convert a valid American quote into its raw implied probability."""
    price = float(odds)
    if not math.isfinite(price) or abs(price) < 100:
        raise ValueError("American odds must be finite with absolute value at least 100")
    return 100 / (price + 100) if price > 0 else abs(price) / (abs(price) + 100)


def no_vig_probabilities(over_odds: int | float, under_odds: int | float) -> tuple[float, float]:
    """Remove vig by proportional normalization of a complete two-way pair."""
    over, under = american_implied_probability(over_odds), american_implied_probability(under_odds)
    total = over + under
    return over / total, under / total


def flat_profit(odds: int | float, grade: str) -> float:
    grade = str(grade).upper()
    if grade == "PUSH": return 0.0
    if grade == "LOSS": return -1.0
    if grade != "WIN": raise ValueError(f"invalid grade: {grade}")
    price = float(odds)
    if not math.isfinite(price) or abs(price) < 100: raise ValueError("invalid American odds")
    return price / 100 if price > 0 else 100 / abs(price)


def edge_bucket(edge: float) -> str:
    # Zero belongs to the non-positive group; all other intervals are [low, high).
    if edge <= 0: return EDGE_BUCKETS[0][2]
    for low, high, label in EDGE_BUCKETS[1:]:
        if low < edge < high or (low in {.02, .05, .10} and edge == low): return label
    raise AssertionError("unreachable")


def opportunity_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Independent decision identity; bookmaker and price are intentionally absent."""
    player_id = normalize_player_id(row.get("canonical_player_id"))
    if player_id is None: raise ValueError("unresolved canonical player identity")
    return (int(row["season"]), int(row["week"]), str(row["game_id"]),
            player_id, str(row["market"]),
            str(row["side"]).upper(), float(row["line"]))


def select_best_prices(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select maximum decimal payout, with stable bookmaker/timestamp tie breaks."""
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows: grouped[opportunity_key(row)].append(row)
    selected=[]
    for key in sorted(grouped):
        # No grade/outcome field participates in this policy.
        chosen=sorted(grouped[key], key=lambda r:(-float(r["decimal_odds"]), str(r["bookmaker"]),
                                                   str(r["quote_timestamp"]), _stable(r)))[0]
        selected.append({**chosen, "opportunity_key": list(key), "selection_policy": "best_decimal_price_then_book_timestamp"})
    return selected


def probability_metrics(rows: Iterable[dict[str, Any]], field: str) -> dict[str, Any]:
    """Binary scores and deterministic decile ECE; pushes are explicitly omitted."""
    binary=[r for r in rows if r.get("grade") in {"WIN", "LOSS"} and r.get(field) is not None]
    pairs=[(min(1-1e-6,max(1e-6,float(r[field]))), 1 if r["grade"] == "WIN" else 0) for r in binary]
    buckets=[]
    for low,high,label in PROBABILITY_BINS:
        values=[(p,y) for p,y in pairs if low <= p < high or (high == 1 and p == 1)]
        if values:
            mean=sum(p for p,_ in values)/len(values); observed=sum(y for _,y in values)/len(values)
            buckets.append({"bucket":label,"count":len(values),"predicted_probability_mean":mean,
                            "observed_win_rate":observed,"absolute_calibration_error":abs(mean-observed)})
    count=len(pairs)
    return {"count":count,"pushes_excluded":sum(r.get("grade")=="PUSH" for r in rows),
            "brier_score":sum((p-y)**2 for p,y in pairs)/count if count else None,
            "log_loss":-sum(y*math.log(p)+(1-y)*math.log(1-p) for p,y in pairs)/count if count else None,
            "ece":sum(b["count"]*b["absolute_calibration_error"] for b in buckets)/count if count else None,
            "bins":buckets}


def _wilson(wins: int, total: int) -> list[float] | None:
    if not total: return None
    z=1.959963984540054; p=wins/total; d=1+z*z/total
    center=(p+z*z/(2*total))/d; half=z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/d
    return [max(0,center-half),min(1,center+half)]


def _cluster_roi(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Fixed-seed game-cluster bootstrap, preserving dependent opportunities."""
    settled=[r for r in rows if r["grade"] in {"WIN","LOSS"}]
    games: dict[str,list[dict[str,Any]]]=defaultdict(list)
    for row in settled: games[str(row["game_id"])].append(row)
    keys=sorted(games)
    if not keys: return {"standard_error":None,"confidence_interval_95":None,"method":"game_cluster_bootstrap"}
    rng=random.Random(1729); estimates=[]
    for _ in range(2000):
        sample=[r for _key in keys for r in games[rng.choice(keys)]]
        estimates.append(sum(float(r["profit_units"]) for r in sample)/len(sample))
    mean=sum(estimates)/len(estimates)
    se=math.sqrt(sum((x-mean)**2 for x in estimates)/(len(estimates)-1))
    estimates.sort()
    return {"standard_error":se,"confidence_interval_95":[estimates[49],estimates[1950]],
            "method":"deterministic_2000_draw_game_cluster_bootstrap","clusters":len(keys)}


def aggregate(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    values=list(rows); wins=sum(r["grade"]=="WIN" for r in values); losses=sum(r["grade"]=="LOSS" for r in values)
    pushes=sum(r["grade"]=="PUSH" for r in values); settled=wins+losses
    profit=sum(float(r["profit_units"]) for r in values)
    avg=lambda field: sum(float(r[field]) for r in values if r.get(field) is not None)/sum(r.get(field) is not None for r in values) if any(r.get(field) is not None for r in values) else None
    return {"opportunities":len(values),"wins":wins,"losses":losses,"pushes":pushes,
            "hit_rate_excluding_pushes":wins/settled if settled else None,"hit_rate_ci_95":_wilson(wins,settled),
            "average_model_probability":avg("model_probability"),"average_market_probability":avg("no_vig_market_probability"),
            "average_edge":avg("edge"),"average_american_odds":avg("american_odds"),
            "units_wagered":settled,"units_profit":profit,"units_returned":settled+profit,
            "roi":profit/settled if settled else None,"roi_uncertainty":_cluster_roi(values),
            "sample_status":"OK" if len(values)>=MINIMUM_SAMPLE else "INSUFFICIENT_SAMPLE"}


def grouped(rows: list[dict[str, Any]], *fields: str) -> list[dict[str, Any]]:
    groups: dict[tuple[str,...],list[dict[str,Any]]]=defaultdict(list)
    for row in rows: groups[tuple(str(row.get(f,"UNKNOWN")) for f in fields)].append(row)
    return [{**{field:key[i] for i,field in enumerate(fields)},**aggregate(groups[key])} for key in sorted(groups)]


def _stable(value: Any) -> str:
    return json.dumps(value,sort_keys=True,separators=(",",":"),default=str)


def _load(path: Path, default: Any) -> Any:
    if not path.exists(): return default
    value=json.loads(path.read_text(encoding="utf-8"))
    return value


def _prediction_index(rows: list[dict[str,Any]]) -> dict[tuple[Any,...],float]:
    result={}
    for row in rows:
        identity = canonical_player_key(row.get("game_id"), first_player_id(
            row.get("canonical_player_id"), row.get("player_id")))
        if identity is None:
            continue
        key=(*identity, str(row.get("market")),float(row.get("line")),
             str(row.get("side") or row.get("selection")).upper())
        probability=row.get("model_probability",row.get("simulation_probability"))
        if probability is not None: result[key]=float(probability)
    return result


def _outcome_rows(directory: Path) -> list[dict[str,Any]]:
    direct=_load(directory/"player_prop_outcomes.json",[])
    if direct: return direct
    stats=_load(directory/"player_stats.json",[])
    return [r for r in stats if r.get("record_role")=="game_outcome" or r.get("is_pregame") is False]


def _validate_and_build(quote: dict[str,Any], game: dict[str,Any], probability: float,
                        pair_probability: float | None,
                        outcomes: dict[tuple[str, str], dict[str, Any]]) -> dict[str,Any]:
    side=str(quote.get("selection") or quote.get("side") or "").upper()
    player_id=first_player_id(quote.get("canonical_player_id"),quote.get("player_id"))
    if not player_id or str(player_id).upper()=="UNKNOWN": raise ValueError("unresolved canonical player identity")
    if side not in {"OVER","UNDER"}: raise ValueError("invalid side")
    try: line=float(quote["line"]); odds=float(quote.get("american_odds",quote.get("odds")))
    except (KeyError,TypeError,ValueError): raise ValueError("invalid line or odds")
    if not math.isfinite(line) or not math.isfinite(odds) or abs(odds) < 100: raise ValueError("invalid line or odds")
    if not 0 <= probability <= 1: raise ValueError("model probability outside [0,1]")
    cutoff=prediction_cutoff(game); timestamp=parse_dt(quote.get("provider_snapshot_timestamp") or quote.get("snapshot_timestamp") or quote.get("captured_at"))
    if not timestamp or not cutoff or timestamp > cutoff: raise ValueError("quote timestamp after or missing prediction cutoff")
    probability_inputs=quote.get("probability_inputs") or quote.get("model_inputs") or []
    if isinstance(probability_inputs,dict): probability_inputs=probability_inputs.keys()
    if any("outcome" in str(value).casefold() or "actual" in str(value).casefold() or "grade" in str(value).casefold()
           for value in probability_inputs):
        raise ValueError("realized outcome declared as a model probability input")
    generated=parse_dt(quote.get("probability_generated_at") or quote.get("prediction_generated_at"))
    if generated and generated > cutoff: raise ValueError("model probability generated after prediction cutoff")
    normalized={**quote,"canonical_player_id":player_id,"selection":side,"line":line}
    graded=grade_quote(normalized,outcomes); grade=str(graded["result"]).upper(); actual=float(graded["actual_stat"])
    expected="PUSH" if actual==line else "WIN" if (actual>line)==(side=="OVER") else "LOSS"
    if grade != expected: raise ValueError("grade contradicts line/outcome")
    decimal=decimal_from_american(odds); edge=probability-pair_probability if pair_probability is not None else None
    return {"season":int(quote.get("season",game.get("season"))),"week":int(quote.get("week",game.get("week"))),
            "game_id":str(game["game_id"]),"canonical_player_id":str(player_id),
            "player_name":quote.get("player_name") or quote.get("canonical_player_name"),"team":quote.get("team"),
            "market":str(quote["market"]),"line":line,"side":side,"bookmaker":str(quote.get("bookmaker") or quote.get("sportsbook")),
            "american_odds":odds,"decimal_odds":decimal,"quote_timestamp":timestamp.isoformat().replace("+00:00","Z"),
            "prediction_cutoff":cutoff.isoformat().replace("+00:00","Z"),"outcome":actual,"grade":grade,
            "model_probability":probability,"no_vig_market_probability":pair_probability,
            "edge":edge,"absolute_edge":abs(edge) if edge is not None else None,
            "edge_direction":"POSITIVE" if edge is not None and edge>0 else "NON_POSITIVE" if edge is not None else None,
            "edge_bucket":edge_bucket(edge) if edge is not None else None,"probability_bucket":next(label for low,high,label in PROBABILITY_BINS if low<=min(probability,1-1e-12)<high),
            "profit_units":flat_profit(odds,grade),"clv":None}


def evaluate(snapshot_root: Path, season: int, start_week: int, end_week: int,
             market: str | None=None, bookmaker: str | None=None) -> dict[str,Any]:
    quotes=[]; accepted=0; incomplete=0; validation=[]
    outcome_diagnostics={"raw_outcome_rows":0,"canonical_player_outcomes":0,
        "duplicate_fields_merged":0,"conflicting_fields":0,
        "missing_requested_stat_count":0,"players_with_multiple_category_rows":0}
    for week in range(start_week,end_week+1):
        directory=snapshot_week_dir(snapshot_root,"nfl",season,week)
        games={str(g["game_id"]):g for g in _load(directory/"games.json",[])}
        raw=_load(directory/"player_prop_odds.json",[]); accepted+=len(raw)
        predictions=_prediction_index(_load(directory/"player_prop_predictions.json",[]))
        raw_outcomes=_outcome_rows(directory)
        try:
            outcomes, week_outcome_diagnostics=aggregate_player_outcomes(raw_outcomes)
        except ValueError as exc:
            raise ValueError(f"integrity validation failed: week={week}, {exc}") from exc
        for field in outcome_diagnostics:
            if field != "missing_requested_stat_count":
                outcome_diagnostics[field] += int(week_outcome_diagnostics[field])
        eligible=[q for q in raw if (not market or q.get("market")==market) and (not bookmaker or str(q.get("bookmaker") or q.get("sportsbook"))==bookmaker)]
        pairs: dict[tuple[Any,...],dict[str,dict[str,Any]]]=defaultdict(dict)
        for q in eligible:
            timestamp=q.get("provider_snapshot_timestamp") or q.get("snapshot_timestamp") or q.get("captured_at")
            identity=canonical_player_key(q.get("game_id"), first_player_id(q.get("canonical_player_id"),q.get("player_id")))
            key=(*(identity or ("", "")),q.get("market"),float(q.get("line")),str(q.get("bookmaker") or q.get("sportsbook")),str(timestamp))
            pairs[key][str(q.get("selection") or q.get("side")).upper()]=q
        novig={}
        for key,sides in pairs.items():
            if set(sides)=={"OVER","UNDER"}:
                over,under=no_vig_probabilities(sides["OVER"].get("american_odds",sides["OVER"].get("odds")),sides["UNDER"].get("american_odds",sides["UNDER"].get("odds")))
                novig[(key,"OVER")]=over; novig[(key,"UNDER")]=under
            else: incomplete+=len(sides)
        for q in eligible:
            side=str(q.get("selection") or q.get("side")).upper(); pid=normalize_player_id(first_player_id(q.get("canonical_player_id"),q.get("player_id"))) or ""
            pkey=(str(q.get("game_id")),pid,str(q.get("market")),float(q.get("line")),side)
            probability=q.get("model_probability",q.get("simulation_probability",predictions.get(pkey)))
            if probability is None: continue
            timestamp=q.get("provider_snapshot_timestamp") or q.get("snapshot_timestamp") or q.get("captured_at")
            pairkey=(str(q.get("game_id")),pid,q.get("market"),float(q.get("line")),str(q.get("bookmaker") or q.get("sportsbook")),str(timestamp))
            try: quotes.append(_validate_and_build(q,games[str(q.get("game_id"))],float(probability),novig.get((pairkey,side)),outcomes))
            except (ValueError,KeyError) as exc:
                if str(exc) == "outcome market is missing":
                    outcome_diagnostics["missing_requested_stat_count"] += 1
                validation.append({"week":week,"game_id":q.get("game_id"),"error":str(exc)})
    if validation: raise ValueError("integrity validation failed: "+_stable(validation[:20]))
    opportunities=select_best_prices(quotes); paired=[r for r in opportunities if r["edge"] is not None]
    model=probability_metrics(opportunities,"model_probability"); market_metrics=probability_metrics(paired,"no_vig_market_probability")
    differences={k:(model[k]-market_metrics[k] if model[k] is not None and market_metrics[k] is not None else None) for k in ("brier_score","log_loss","ece")}
    positive=[r for r in paired if r["edge"]>0]; positive_metrics=aggregate(positive)
    breakdowns={"market":grouped(paired,"market"),"week":grouped(paired,"week"),"bookmaker":grouped(paired,"bookmaker"),
                "side":grouped(paired,"side"),"edge_bucket":grouped(paired,"edge_bucket"),
                "market_edge_bucket":grouped(paired,"market","edge_bucket"),"bookmaker_market":grouped(paired,"bookmaker","market"),
                "probability_bucket":grouped(paired,"probability_bucket")}
    summary={"schema_version":1,"season":season,"weeks":[start_week,end_week],"accepted_quotes":accepted,
             "gradeable_quotes":len(quotes),"unique_opportunities":len(opportunities),"paired_opportunities":len(paired),
             "incomplete_pair_quotes":incomplete,"pushes":sum(r["grade"]=="PUSH" for r in opportunities),
             "positive_edge_opportunities":len(positive),"positive_edge_profit":positive_metrics["units_profit"],
             "positive_edge_roi":positive_metrics["roi"],"clv_ready":False,"clv_reason":CLV_REASON,
             "integrity_validation":"PASS","minimum_sample_threshold":MINIMUM_SAMPLE,
             "outcome_aggregation":outcome_diagnostics}
    return {"summary":summary,"quote_rows":quotes,"opportunity_rows":opportunities,
            "calibration":{"model":model,"market":market_metrics,"model_minus_market":differences},
            "edge_buckets":breakdowns["edge_bucket"],"breakdowns":breakdowns}


def write_outputs(report: dict[str,Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True,exist_ok=True)
    artifacts={"evaluation_summary.json":report["summary"],"evaluation_rows.json":{"quote_level":report["quote_rows"],"opportunity_level":report["opportunity_rows"]},
               "calibration.json":report["calibration"],"edge_buckets.json":report["edge_buckets"],
               "market_breakdown.json":report["breakdowns"]["market"],"bookmaker_breakdown.json":report["breakdowns"]["bookmaker"],
               "evaluation_breakdowns.json":report["breakdowns"]}
    for name,value in artifacts.items(): (output_dir/name).write_text(json.dumps(value,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    rows=report["opportunity_rows"]
    with (output_dir/"opportunity_rows.csv").open("w",newline="",encoding="utf-8") as handle:
        writer=csv.DictWriter(handle,fieldnames=sorted({key for row in rows for key in row}),extrasaction="ignore"); writer.writeheader()
        for row in rows: writer.writerow({k:_stable(v) if isinstance(v,(list,dict)) else v for k,v in row.items()})
    manifest={name:hashlib.sha256((output_dir/name).read_bytes()).hexdigest() for name in sorted([*artifacts,"opportunity_rows.csv"])}
    (output_dir/"manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n")


def _fmt(value: Any) -> str: return "N/A" if value is None else f"{value:.6f}" if isinstance(value,float) else str(value)


def main(argv: list[str] | None=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("--season",type=int,required=True)
    parser.add_argument("--start-week",type=int,default=1); parser.add_argument("--end-week",type=int,default=1)
    parser.add_argument("--snapshot-root",type=Path,default=SNAPSHOTS_DIR); parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--market",choices=CANONICAL_PLAYER_PROP_MARKETS); parser.add_argument("--bookmaker")
    args=parser.parse_args(argv); report=evaluate(args.snapshot_root,args.season,args.start_week,args.end_week,args.market,args.bookmaker); write_outputs(report,args.output_dir)
    s=report["summary"]; c=report["calibration"]
    lines=[f"NFL player props: {s['season']} weeks {s['weeks'][0]}-{s['weeks'][1]}",f"Accepted quotes: {s['accepted_quotes']}; gradeable: {s['gradeable_quotes']}; unique opportunities: {s['unique_opportunities']}; pushes: {s['pushes']}",
           f"Model Brier/log loss: {_fmt(c['model']['brier_score'])} / {_fmt(c['model']['log_loss'])}",f"Market Brier/log loss: {_fmt(c['market']['brier_score'])} / {_fmt(c['market']['log_loss'])}",
           f"Positive edge: {s['positive_edge_opportunities']} opportunities; profit {_fmt(s['positive_edge_profit'])}; ROI {_fmt(s['positive_edge_roi'])}",
           f"CLV_READY = {str(s['clv_ready']).lower()} ({s['clv_reason']})",f"Integrity validation: {s['integrity_validation']}"]
    markets=[x for x in report["breakdowns"]["market"] if x["roi"] is not None]
    if markets:
        best=max(markets,key=lambda x:(x["roi"],x["market"])); worst=min(markets,key=lambda x:(x["roi"],x["market"]))
        lines.insert(-2,f"Market ROI range (descriptive): best {best['market']} {_fmt(best['roi'])} (n={best['opportunities']}), worst {worst['market']} {_fmt(worst['roi'])} (n={worst['opportunities']})")
    print("\n".join(lines)); return 0


if __name__ == "__main__": raise SystemExit(main())
