"""Deterministic, offline NFL player-prop feature attribution and error analysis.

The attribution in this module is observational.  It explains associations in
persisted simulation summaries and provenance; it does not claim causal model
coefficients or refit/tune the model.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from .config import SNAPSHOTS_DIR
from .evaluate_nfl_player_props import probability_metrics
from .snapshots import snapshot_week_dir


JSON_ARTIFACTS = (
    "error_analysis_summary.json",
    "feature_attribution.json",
    "market_overconfidence.json",
    "segment_metrics.json",
    "mean_variance_diagnostics.json",
    "roi_loss_contributors.json",
)
CSV_ARTIFACTS = (
    "feature_attribution.csv",
    "segment_metrics.csv",
    "roi_loss_contributors.csv",
)
NUMERIC_FIELDS = {
    "season", "week", "line", "model_probability", "no_vig_market_probability",
    "edge", "absolute_edge", "profit_units", "outcome", "american_odds",
    "decimal_odds",
}
FEATURES = (
    "line",
    "simulated_mean",
    "simulated_median",
    "simulated_stddev",
    "support_width",
    "zero_mass",
    "unique_values",
    "mean_minus_line",
    "standardized_mean_line_gap",
    "relative_dispersion",
    "player_history_games",
    "team_history_rows",
    "league_team_history_rows",
)


def _read_json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({field for row in rows for field in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, sort_keys=True, separators=(",", ":"))
                             if isinstance(value, (dict, list)) else value for key, value in row.items()})


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _semantic_hash(path: Path) -> str:
    if path.suffix == ".json":
        value = _read_json(path, None)
        if isinstance(value, list):
            value = sorted(value, key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":")))
    elif path.suffix == ".csv":
        with path.open(encoding="utf-8", newline="") as handle:
            value = sorted((dict(row) for row in csv.DictReader(handle)),
                           key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":")))
    else:
        return _hash(path)
    payload=json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _load_opportunities(path: Path, season: int, start_week: int, end_week: int) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            row = dict(raw)
            for field in NUMERIC_FIELDS:
                if field in row and row[field] not in (None, ""):
                    row[field] = _number(row[field])
            if int(row.get("season") or -1) != season:
                continue
            week = int(row.get("week") or -1)
            if start_week <= week <= end_week:
                rows.append(row)
    return sorted(rows, key=_side_key)


def _player_id(row: dict[str, Any]) -> str:
    value = row.get("canonical_player_id") or row.get("player_id") or ""
    number = _number(value)
    return str(int(number)) if number is not None and number.is_integer() else str(value).strip()


def _base_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (int(row.get("season") or 0), int(row.get("week") or 0), str(row.get("game_id") or ""),
            _player_id(row), str(row.get("market") or ""), float(row.get("line") or 0))


def _side_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (*_base_key(row), str(row.get("side") or ""), str(row.get("bookmaker") or ""))


def _prediction_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (*_base_key(row), str(row.get("side") or "").upper())


def _position_index(snapshot_root: Path, season: int, start_week: int, end_week: int) -> tuple[dict[tuple[str, str], str], list[Path]]:
    index: dict[tuple[str, str], str] = {}
    inputs=[]
    for week in range(start_week, end_week + 1):
        directory = snapshot_week_dir(snapshot_root, "nfl", season, week)
        path=directory/"player_identities.json"
        if path.exists(): inputs.append(path)
        for row in _read_json(path, []):
            position = str(row.get("position") or "").upper()
            key = (str(row.get("game_id") or ""), _player_id(row))
            if position and key not in index:
                index[key] = position
    return index,inputs


def _predictions(snapshot_root: Path, season: int, start_week: int, end_week: int) -> tuple[dict[tuple[Any, ...], dict[str, Any]], list[Path]]:
    index = {}
    inputs = []
    for week in range(start_week, end_week + 1):
        path = snapshot_week_dir(snapshot_root, "nfl", season, week) / "player_prop_predictions.json"
        if not path.exists():
            continue
        inputs.append(path)
        for row in _read_json(path, []):
            key = _prediction_key(row)
            old = index.get(key)
            if old is not None and old != row:
                raise ValueError(f"conflicting prediction rows for canonical key {key}")
            index[key] = row
    return index, inputs


def _infer_archetypes(rows: list[dict[str, Any]], positions: dict[tuple[str, str], str]) -> dict[tuple[str, str], str]:
    markets: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in rows:
        markets[(str(row.get("game_id") or ""), _player_id(row))].add(str(row.get("market") or ""))
    result = {}
    for key, names in markets.items():
        position = positions.get(key)
        if position in {"QB", "RB", "FB", "WR", "TE"}:
            result[key] = position
        elif any(name.startswith("passing_") for name in names) and any(name.startswith("rushing_") for name in names):
            result[key] = "DUAL_THREAT_PASSER"
        elif any(name.startswith("passing_") for name in names):
            result[key] = "PASSER"
        elif any(name.startswith("rushing_") for name in names) and any(name in {"receptions", "receiving_yards"} for name in names):
            result[key] = "SCRIMMAGE_HYBRID"
        elif any(name.startswith("rushing_") for name in names):
            result[key] = "BALL_CARRIER"
        elif any(name in {"receptions", "receiving_yards"} for name in names):
            result[key] = "RECEIVER"
        else:
            result[key] = "UNKNOWN"
    return result


def _join(opportunities: list[dict[str, Any]], predictions: dict[tuple[Any, ...], dict[str, Any]],
          archetypes: dict[tuple[str, str], str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    joined, exclusions = [], []
    for row in opportunities:
        prediction = predictions.get(_prediction_key(row))
        if prediction is None:
            exclusions.append({"key": list(_prediction_key(row)), "reason": "PREDICTION_NOT_FOUND"})
            continue
        summary = prediction.get("distribution_summary") or {}
        required = ("mean", "median", "standard_deviation", "minimum", "maximum")
        if any(_number(summary.get(field)) is None for field in required):
            exclusions.append({"key": list(_prediction_key(row)), "reason": "DISTRIBUTION_SUMMARY_MISSING"})
            continue
        mean = float(summary["mean"]); median = float(summary["median"])
        stddev = float(summary["standard_deviation"]); line = float(row["line"])
        minimum = float(summary["minimum"]); maximum = float(summary["maximum"])
        provenance = prediction.get("provenance") or {}
        probability = float(row["model_probability"])
        actual = float(row["outcome"])
        outcome = 1.0 if str(row.get("grade")).upper() == "WIN" else 0.0
        features = {
            "line":line, "simulated_mean":mean, "simulated_median":median,
            "simulated_stddev":stddev, "support_width":maximum-minimum,
            "zero_mass":_number(summary.get("zero_mass")), "unique_values":_number(summary.get("unique_values")),
            "mean_minus_line":mean-line,
            "standardized_mean_line_gap":(mean-line)/max(stddev, 1e-9),
            "relative_dispersion":stddev/(abs(mean)+1.0),
            "player_history_games":_number(provenance.get("player_history_games")),
            "team_history_rows":_number(provenance.get("team_history_rows")),
            "league_team_history_rows":_number(provenance.get("league_team_history_rows")),
        }
        joined.append({**row, **features, "actual_stat":actual,
                       "probability_extremeness":2*abs(probability-.5),
                       "brier_error":(probability-outcome)**2,
                       "log_loss":-(outcome*math.log(max(probability,1e-15))+(1-outcome)*math.log(max(1-probability,1e-15))),
                       "simulation_minimum":minimum,"simulation_maximum":maximum,
                       "simulation_p05":_number((summary.get("quantiles") or {}).get("p05")),
                       "simulation_p95":_number((summary.get("quantiles") or {}).get("p95")),
                       "archetype":archetypes.get((str(row.get("game_id") or ""),_player_id(row)),"UNKNOWN")})
    return sorted(joined, key=_side_key), sorted(exclusions, key=lambda row: json.dumps(row, sort_keys=True))


def _mean(values: Iterable[float | None]) -> float | None:
    present = [float(value) for value in values if value is not None]
    return sum(present)/len(present) if present else None


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    mx=sum(xs)/len(xs); my=sum(ys)/len(ys)
    dx=[x-mx for x in xs]; dy=[y-my for y in ys]
    denominator=math.sqrt(sum(x*x for x in dx)*sum(y*y for y in dy))
    return sum(x*y for x,y in zip(dx,dy))/denominator if denominator else None


def _ranks(values: list[float]) -> list[float]:
    order=sorted(range(len(values)),key=lambda index:(values[index],index)); result=[0.0]*len(values)
    start=0
    while start < len(order):
        end=start+1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        rank=(start+end-1)/2+1
        for index in order[start:end]: result[index]=rank
        start=end
    return result


def _attribution(rows: list[dict[str, Any]], target: str, scope: str = "ALL") -> list[dict[str, Any]]:
    result=[]
    for feature in FEATURES:
        pairs=[(float(row[feature]),float(row[target])) for row in rows if row.get(feature) is not None and row.get(target) is not None]
        xs=[pair[0] for pair in pairs]; ys=[pair[1] for pair in pairs]
        extreme=[float(row[feature]) for row in rows if row.get(feature) is not None and (float(row["model_probability"]) <= .05 or float(row["model_probability"]) >= .95)]
        ordinary=[float(row[feature]) for row in rows if row.get(feature) is not None and .05 < float(row["model_probability"]) < .95]
        row={"scope":scope,"target":target,"feature":feature,"count":len(pairs),
             "pearson":_pearson(xs,ys),"spearman":_pearson(_ranks(xs),_ranks(ys)) if pairs else None,
             "extreme_mean":_mean(extreme),"non_extreme_mean":_mean(ordinary),
             "extreme_minus_non_extreme":None if not extreme or not ordinary else _mean(extreme)-_mean(ordinary)}
        result.append(row)
    result=sorted(result,key=lambda row:(-(abs(row["spearman"]) if row["spearman"] is not None else -1),row["feature"]))
    for rank,row in enumerate(result,1):
        row["rank"]=rank
        row["feature_class"]="history_depth" if row["feature"].endswith("history_games") or row["feature"].endswith("history_rows") else "distribution_geometry"
    return result


def _one_per_base(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows: groups[_base_key(row)].append(row)
    return [sorted(groups[key],key=lambda row:(str(row.get("side")) != "OVER",_side_key(row)))[0]
            for key in sorted(groups)]


def _metric(rows: list[dict[str, Any]]) -> dict[str, Any]:
    model=probability_metrics(rows,"model_probability"); market=probability_metrics(rows,"no_vig_market_probability")
    settled=[row for row in rows if str(row.get("grade")).upper() in {"WIN","LOSS"}]
    profit=sum(float(row.get("profit_units") or 0) for row in settled)
    return {"count":len(rows),"weeks":sorted({int(row["week"]) for row in rows}),
            "model_brier":model["brier_score"],"model_log_loss":model["log_loss"],"model_ece":model["ece"],
            "market_brier":market["brier_score"],"market_log_loss":market["log_loss"],"market_ece":market["ece"],
            "model_minus_market_brier":None if model["brier_score"] is None or market["brier_score"] is None else model["brier_score"]-market["brier_score"],
            "model_minus_market_log_loss":None if model["log_loss"] is None or market["log_loss"] is None else model["log_loss"]-market["log_loss"],
            "roi":profit/len(settled) if settled else None,"units_profit":profit}


def _grouped(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows: groups[tuple(str(row.get(field) or "UNKNOWN") for field in fields)].append(row)
    return [{**{field:key[index] for index,field in enumerate(fields)},**_metric(groups[key])}
            for key in sorted(groups)]


def _market_overconfidence(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str,list[dict[str,Any]]] = defaultdict(list)
    for row in rows: groups[str(row["market"])].append(row)
    result=[]
    for market in sorted(groups):
        values=groups[market]; weekly=[]
        for week in sorted({int(row["week"]) for row in values}):
            subset=[row for row in values if int(row["week"])==week]
            metric=_metric(subset); extreme=[row for row in subset if float(row["model_probability"])<=.05 or float(row["model_probability"])>=.95]
            accuracy=_mean(1.0 if (float(row["model_probability"])>=.5)==(str(row["grade"]).upper()=="WIN") else 0.0 for row in extreme)
            confidence=_mean(max(float(row["model_probability"]),1-float(row["model_probability"])) for row in extreme)
            gap=None if accuracy is None or confidence is None else confidence-accuracy
            weekly.append({"week":week,"count":len(subset),"model_ece":metric["model_ece"],"extreme_count":len(extreme),"extreme_confidence_accuracy_gap":gap,
                           "overconfident":metric["model_ece"] is not None and metric["model_ece"]>.15 and gap is not None and gap>.10})
        base=_metric(values); over=sum(item["overconfident"] for item in weekly)
        result.append({"market":market,**base,"weekly":weekly,"overconfident_weeks":over,
                       "overconfident_week_rate":over/len(weekly) if weekly else None,
                       "consistency_status":"CONSISTENTLY_OVERCONFIDENT" if len(weekly)>=2 and over/len(weekly)>=.75 else "INSUFFICIENT_MULTIWEEK_EVIDENCE" if len(weekly)<2 else "NOT_CONSISTENT"})
    return sorted(result,key=lambda row:(-(row["model_minus_market_brier"] or -math.inf),row["market"]))


def _mean_variance(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str,list[dict[str,Any]]] = defaultdict(list)
    for row in _one_per_base(rows): groups[str(row["market"])].append(row)
    result=[]
    for market in sorted(groups):
        values=groups[market]; residuals=[float(row["actual_stat"])-float(row["simulated_mean"]) for row in values]
        bias=_mean(residuals); mae=_mean(abs(value) for value in residuals); rmse=math.sqrt(_mean(value*value for value in residuals) or 0)
        average_sd=_mean(float(row["simulated_stddev"]) for row in values)
        coverage_rows=[row for row in values if row.get("simulation_p05") is not None and row.get("simulation_p95") is not None]
        coverage=_mean(1.0 if float(row["simulation_p05"])<=float(row["actual_stat"])<=float(row["simulation_p95"]) else 0.0 for row in coverage_rows)
        outside=_mean(1.0 if float(row["actual_stat"])<float(row["simulation_minimum"]) or float(row["actual_stat"])>float(row["simulation_maximum"]) else 0.0 for row in values)
        ratio=None if not average_sd else rmse/average_sd
        mean_issue=bool(rmse and bias is not None and abs(bias)/rmse>=.35)
        variance_issue=bool((ratio is not None and ratio>1.25) or (coverage is not None and coverage<.80))
        diagnosis="MEAN_AND_VARIANCE" if mean_issue and variance_issue else "MEAN_BIAS" if mean_issue else "VARIANCE_TOO_LOW" if variance_issue else "NO_CLEAR_SIGNAL"
        result.append({"market":market,"base_opportunities":len(values),"mean_bias_actual_minus_simulated":bias,
                       "mean_absolute_error":mae,"root_mean_squared_error":rmse,"average_predicted_stddev":average_sd,
                       "rmse_to_predicted_stddev_ratio":ratio,"p05_p95_empirical_coverage":coverage,
                       "actual_outside_simulation_support_rate":outside,"diagnosis":diagnosis})
    return sorted(result,key=lambda row:(-float(row["rmse_to_predicted_stddev_ratio"] or 0),row["market"]))


def _roi_losses(rows: list[dict[str, Any]], top_n: int) -> dict[str, Any]:
    selected=[row for row in rows if float(row.get("edge") or 0)>0]
    losing=[row for row in selected if float(row.get("profit_units") or 0)<0]
    total_loss=-sum(float(row["profit_units"]) for row in losing)
    losing=sorted(losing,key=lambda row:(float(row["profit_units"]),-float(row.get("edge") or 0),_side_key(row)))
    top=[]; cumulative=0.0
    for rank,row in enumerate(losing[:top_n],1):
        loss=-float(row["profit_units"]); cumulative += loss
        top.append({"rank":rank,"season":int(row["season"]),"week":int(row["week"]),"game_id":row["game_id"],
                    "canonical_player_id":_player_id(row),"player_name":row.get("player_name"),"team":row.get("team"),
                    "archetype":row.get("archetype"),"market":row["market"],"side":row["side"],"line":row["line"],
                    "model_probability":row["model_probability"],"market_probability":row.get("no_vig_market_probability"),
                    "edge":row.get("edge"),"american_odds":row.get("american_odds"),"actual_stat":row.get("actual_stat"),
                    "profit_units":row["profit_units"],"share_of_negative_units":loss/total_loss if total_loss else None,
                    "cumulative_share_of_negative_units":cumulative/total_loss if total_loss else None})
    base_groups: dict[tuple[Any,...],list[dict[str,Any]]] = defaultdict(list)
    for row in rows: base_groups[_base_key(row)].append(row)
    base_losses=[]
    for key,values in base_groups.items():
        profit=sum(float(row.get("profit_units") or 0) for row in values)
        if profit<0:
            first=sorted(values,key=_side_key)[0]
            base_losses.append({"season":key[0],"week":key[1],"game_id":key[2],"canonical_player_id":key[3],
                                "player_name":first.get("player_name"),"team":first.get("team"),"archetype":first.get("archetype"),
                                "market":key[4],"line":key[5],"combined_side_profit_units":profit})
    return {"selection_policy":"positive model edge only","selected_wagers":len(selected),"losing_wagers":len(losing),
            "total_negative_units":total_loss,"top_losing_wagers":top,
            "worst_base_opportunities":sorted(base_losses,key=lambda row:(row["combined_side_profit_units"],row["week"],row["game_id"],row["canonical_player_id"],row["market"],row["line"]))[:top_n],
            "loss_by_market":sorted(_grouped(losing,("market",)),key=lambda row:(row["units_profit"],row["market"])),
            "loss_by_team":sorted(_grouped(losing,("team",)),key=lambda row:(row["units_profit"],row["team"])),
            "loss_by_archetype":sorted(_grouped(losing,("archetype",)),key=lambda row:(row["units_profit"],row["archetype"]))}


def analyze(*, season: int, start_week: int, end_week: int, snapshot_root: Path,
            season_results_dir: Path, output_dir: Path, top_n: int = 50,
            min_segment_size: int = 20) -> dict[str, Any]:
    if start_week>end_week: raise ValueError("start week must not exceed end week")
    opportunity_path=season_results_dir/"opportunity_rows.csv"
    if not opportunity_path.exists(): raise FileNotFoundError(f"missing season opportunity artifact: {opportunity_path}")
    opportunities=_load_opportunities(opportunity_path,season,start_week,end_week)
    predictions,prediction_paths=_predictions(snapshot_root,season,start_week,end_week)
    positions,identity_paths=_position_index(snapshot_root,season,start_week,end_week)
    archetypes=_infer_archetypes(list(predictions.values()),positions)
    joined,exclusions=_join(opportunities,predictions,archetypes)
    base_rows=_one_per_base(joined)
    attribution=_attribution(base_rows,"probability_extremeness")+_attribution(base_rows,"brier_error")
    market_attribution=[]
    for market in sorted({str(row["market"]) for row in base_rows}):
        subset=[row for row in base_rows if row["market"]==market]
        market_attribution += _attribution(subset,"probability_extremeness",market)
    feature_report={"method":"observational univariate Pearson/Spearman association on one deterministic OVER-preferred row per base opportunity",
                    "causal":False,"extreme_probability_threshold":"p <= 0.05 or p >= 0.95",
                    "global":attribution,"by_market":market_attribution}
    market_report=_market_overconfidence(joined)
    segments=[]
    for fields in (("team",),("archetype",),("team","market"),("archetype","market")):
        kind="_by_".join(fields)
        for row in _grouped(joined,fields):
            segments.append({"segment_type":kind,"eligible_for_ranking":row["count"]>=min_segment_size,**row})
    segments=sorted(segments,key=lambda row:(row["segment_type"],-(row["model_minus_market_brier"] or -math.inf),*(str(row.get(field) or "") for field in ("team","archetype","market"))))
    mean_variance=_mean_variance(joined)
    roi=_roi_losses(joined,top_n)
    ranked_segments=[row for row in segments if row["eligible_for_ranking"] and row["model_minus_market_brier"] is not None]
    ranked_segments=sorted(ranked_segments,key=lambda row:(-row["model_minus_market_brier"],row["segment_type"]))
    poorly_modeled=ranked_segments[:top_n]
    poorly_modeled_teams=[row for row in ranked_segments if row["segment_type"]=="team"][:top_n]
    poorly_modeled_archetypes=[row for row in ranked_segments if row["segment_type"]=="archetype"][:top_n]
    overconfident=[row for row in market_report if row["consistency_status"]=="CONSISTENTLY_OVERCONFIDENT"]
    currently_overconfident=[row["market"] for row in market_report if row["model_ece"] is not None and row["model_ece"]>.15 and (row["model_minus_market_brier"] or 0)>.05]
    insufficient=[row["market"] for row in market_report if row["consistency_status"]=="INSUFFICIENT_MULTIWEEK_EVIDENCE"]
    evaluated_weeks=sorted({int(row["week"]) for row in joined})
    summary={"schema_version":1,"season":season,"weeks":[start_week,end_week],"network_contacted":False,
             "evaluated_weeks":evaluated_weeks,"missing_evaluation_weeks":[week for week in range(start_week,end_week+1) if week not in evaluated_weeks],
             "coverage_status":"MULTIWEEK" if len(evaluated_weeks)>=2 else "SINGLE_WEEK_ONLY" if evaluated_weeks else "NO_GRADEABLE_DATA",
             "opportunity_rows":len(opportunities),"joined_side_forecasts":len(joined),"base_opportunities":len(base_rows),
             "excluded_rows":len(exclusions),"exclusions_by_reason":dict(sorted(Counter(row["reason"] for row in exclusions).items())),
             "answers":{"strongest_extreme_probability_drivers":_attribution(base_rows,"probability_extremeness")[:5],
                        "consistently_overconfident_markets":[row["market"] for row in overconfident],
                        "currently_overconfident_markets":currently_overconfident,
                        "markets_with_insufficient_multiweek_evidence":insufficient,
                        "poorly_modeled_segments":poorly_modeled,
                        "poorly_modeled_teams":poorly_modeled_teams,
                        "poorly_modeled_archetypes":poorly_modeled_archetypes,
                        "mean_vs_variance":mean_variance,
                        "largest_roi_loss_contributors":roi["top_losing_wagers"][:10]},
             "methodology_notes":["Feature attribution is observational and does not represent causal coefficients.",
                                  "Only persisted distribution geometry and history-depth provenance are attributable; raw simulator feature vectors are not persisted.",
                                  "Mean-versus-variance diagnosis uses simulated summary residuals and p05-p95 coverage.",
                                  "Consistent overconfidence requires at least two evaluated weeks.",
                                  "ROI contributors are restricted to positive-model-edge opportunities."]}
    artifacts={"error_analysis_summary.json":summary,"feature_attribution.json":feature_report,
               "market_overconfidence.json":market_report,"segment_metrics.json":segments,
               "mean_variance_diagnostics.json":mean_variance,"roi_loss_contributors.json":roi}
    output_dir.mkdir(parents=True,exist_ok=True)
    for name,value in artifacts.items(): _write_json(output_dir/name,value)
    _write_csv(output_dir/"feature_attribution.csv",attribution+market_attribution)
    _write_csv(output_dir/"segment_metrics.csv",segments)
    _write_csv(output_dir/"roi_loss_contributors.csv",roi["top_losing_wagers"])
    input_paths=[opportunity_path,*prediction_paths,*identity_paths]
    manifest={"schema_version":1,"network_contacted":False,
              "config":{"season":season,"start_week":start_week,"end_week":end_week,"top_n":top_n,"min_segment_size":min_segment_size},
              "inputs":{str(path.relative_to(snapshot_root.parent.parent) if path.is_relative_to(snapshot_root.parent.parent) else path.name):_semantic_hash(path) for path in sorted(input_paths)},
              "artifacts":{name:_hash(output_dir/name) for name in sorted((*JSON_ARTIFACTS,*CSV_ARTIFACTS))}}
    _write_json(output_dir/"analysis_manifest.json",manifest)
    return {**artifacts,"analysis_manifest.json":manifest}


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season",type=int,required=True); parser.add_argument("--start-week",type=int,default=1); parser.add_argument("--end-week",type=int,default=18)
    parser.add_argument("--snapshot-root",type=Path,default=SNAPSHOTS_DIR); parser.add_argument("--season-results-dir",type=Path,required=True)
    parser.add_argument("--output-dir",type=Path,required=True); parser.add_argument("--top-n",type=int,default=50); parser.add_argument("--min-segment-size",type=int,default=20)
    analyze(**vars(parser.parse_args(argv))); return 0


if __name__ == "__main__": raise SystemExit(main())
