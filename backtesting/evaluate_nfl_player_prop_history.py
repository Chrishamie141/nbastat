"""Deterministic, offline season orchestration for NFL player-prop evaluation.

This module deliberately imports only the three offline pipeline stages.  It
does not contain provider acquisition code and never opens a network client.
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

from .audit_nfl_player_prop_predictions import audit
from .build_nfl_player_prop_predictions import MODEL_VERSIONS, build
from .config import SNAPSHOTS_DIR
from .evaluate_nfl_player_props import aggregate, evaluate, grouped, probability_metrics
from .markets import CANONICAL_PLAYER_PROP_MARKETS
from .snapshots import snapshot_week_dir

STATUSES = ("COMPLETE", "PARTIAL", "MISSING_SNAPSHOTS", "MISSING_QUOTES",
            "MISSING_OUTCOMES", "PREDICTIONS_NOT_READY",
            "RECOVERABLE_EVALUATION_ERROR", "INTEGRITY_FAILURE")
INSPECTED_FILES = ("games.json", "player_prop_odds.json", "player_prop_predictions.json",
                   "player_stats.json", "player_identities.json", "manifest.json")
ARTIFACTS = ("season_summary.json", "weekly_metrics.json", "market_metrics.json",
             "side_metrics.json", "bookmaker_metrics.json", "calibration_by_week.json",
             "calibration_by_market.json", "roi_by_week.json", "roi_by_market.json",
             "coverage_by_week.json", "exclusions_summary.json", "readiness_summary.json",
             "probability_direction_audit.json", "distribution_diagnostics.json",
             "systemic_findings.json", "failed_weeks.json")


def _json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _input_manifest(directory: Path, config: dict[str, Any]) -> dict[str, Any]:
    return {"config": config, "inputs": {name: _hash(directory / name)
            for name in INSPECTED_FILES if (directory / name).exists()}}


def _metric(rows: list[dict[str, Any]], transform: str = "current") -> dict[str, Any]:
    changed = []
    for row in rows:
        p = float(row["model_probability"])
        if transform == "complement": p = 1 - p
        elif transform == "swap": p = float(row.get("opposite_probability") if row.get("opposite_probability") is not None else 1-p-float(row.get("push_probability") or 0))
        changed.append({**row, "p": p})
    return probability_metrics(changed, "p")


def _direction(rows: list[dict[str, Any]]) -> dict[str, Any]:
    current, swap, complement = _metric(rows), _metric(rows, "swap"), _metric(rows, "complement")
    result = {"current": current, "over_under_swapped": swap, "one_minus_current": complement}
    for variant, metric in (("swap", swap), ("complement", complement)):
        result[f"brier_improvement_from_{variant}"] = (None if current["brier_score"] is None or metric["brier_score"] is None else current["brier_score"]-metric["brier_score"])
        result[f"log_loss_improvement_from_{variant}"] = (None if current["log_loss"] is None or metric["log_loss"] is None else current["log_loss"]-metric["log_loss"])
    return result


def _mean(values: Iterable[Any]) -> float | None:
    values = [float(v) for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, sort_keys=True, separators=(",", ":")) if isinstance(v, (dict, list)) else v for k, v in row.items()})


def _finding(code: str, severity: str, scope: str, metric: Any, threshold: str,
             rows: list[dict[str, Any]], next_step: str) -> dict[str, Any]:
    return {"code": code, "severity": severity, "scope": scope,
            "supporting_metric": metric, "threshold": threshold,
            "affected_weeks": sorted({r.get("week") for r in rows if r.get("week") is not None}),
            "affected_markets": sorted({r.get("market") for r in rows if r.get("market") is not None}),
            "evidence_count": len(rows), "recommended_next_diagnostic_step": next_step}


def systemic_findings(direction: dict[str, Any], opportunities: list[dict[str, Any]],
                      coverage: list[dict[str, Any]], distributions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    cur = direction["global"]["current"]; comp = direction["global"]["one_minus_current"]
    if cur["count"] < 30:
        findings.append(_finding("SAMPLE_TOO_SMALL", "WARNING", "SEASON", cur["count"], "count < 30", opportunities, "Collect more completed, gradeable weeks."))
    if cur["brier_score"] is not None and comp["brier_score"] < cur["brier_score"] * .75:
        findings.append(_finding("PROBABILITY_INVERSION_LIKELY", "CRITICAL", "SEASON", cur["brier_score"]-comp["brier_score"], "complemented Brier improves by >25%", opportunities, "Audit probability lookup and direction without changing forecasts."))
    extreme = [r for r in opportunities if float(r["model_probability"]) < .05 or float(r["model_probability"]) > .95]
    if len(extreme) >= 30 and cur["ece"] is not None and cur["ece"] > .15:
        findings.append(_finding("GLOBAL_OVERCONFIDENCE", "HIGH", "SEASON", cur["ece"], "ECE > 0.15 with >=30 extreme forecasts", extreme, "Inspect simulation dispersion and feature-history depth."))
    low = [r for r in coverage if (r.get("ready_coverage") or 0) < .5 and r.get("historical_quote_rows", 0)]
    if low: findings.append(_finding("LOW_READY_COVERAGE", "WARNING", "WEEK", _mean(r["ready_coverage"] for r in low), "ready coverage < 50%", low, "Inspect readiness reasons and local feature history."))
    gaps = [r for r in coverage if r.get("outcome_not_found", 0) or r.get("outcome_market_missing", 0)]
    if gaps: findings.append(_finding("OUTCOME_COVERAGE_GAP", "WARNING", "WEEK", sum(r.get("outcome_not_found",0)+r.get("outcome_market_missing",0) for r in gaps), "> 0 outcome exclusions", gaps, "Repair outcome snapshot coverage; do not attribute this to the model."))
    failed = [r for r in coverage if r["status"] not in {"COMPLETE", "PARTIAL"}]
    if failed: findings.append(_finding("WEEK_SPECIFIC_DATA_FAILURE", "WARNING", "WEEK", len(failed), "> 0 unavailable weeks", failed, "Acquire or rebuild the named local snapshot artifacts."))
    outside = [r for r in distributions if r.get("line_outside_support")]
    if outside: findings.append(_finding("LINE_OUTSIDE_SIMULATION_SUPPORT", "HIGH", "MARKET", len(outside)/max(1,len(distributions)), "> 0 lines outside support", outside, "Inspect feature history and simulated support; do not tune in this audit."))
    return sorted(findings, key=lambda x: (x["code"], x["scope"]))


def run(*, season: int, start_week: int, end_week: int, snapshot_root: Path,
        output_dir: Path, model_version: str, simulations: int, seed: int,
        build_missing_predictions: bool = False, overwrite_predictions: bool = False,
        continue_on_error: bool = False, strict_outcomes: bool = False,
        market: str | None = None, bookmaker: str | None = None,
        validate: bool = False, resume: bool = False) -> dict[str, Any]:
    if start_week > end_week: raise ValueError("start week must not exceed end week")
    output_dir.mkdir(parents=True, exist_ok=True)
    config = {"season":season,"model_version":model_version,"simulations":simulations,"seed":seed,
              "build_missing_predictions":build_missing_predictions,
              "overwrite_predictions":overwrite_predictions,"strict_outcomes":strict_outcomes,
              "market":market,"bookmaker":bookmaker,"validate":validate}
    reports=[]; failed=[]; reused=[]; recomputed=[]
    for week in range(start_week, end_week + 1):
        directory=snapshot_week_dir(snapshot_root,"nfl",season,week); cache=output_dir/"weeks"/f"week_{week:02d}.json"
        inputs=_input_manifest(directory, config)
        if resume and cache.exists():
            saved=_json(cache,{}); expected=saved.get("resume_manifest")
            if expected == inputs and saved.get("status") in {"COMPLETE","PARTIAL"}:
                reports.append(saved); reused.append(week); continue
        recomputed.append(week); missing=[n for n in INSPECTED_FILES if not (directory/n).exists()]
        quotes=_json(directory/"player_prop_odds.json",[]); outcomes=_json(directory/"player_stats.json",[])
        reasons=[]; status="COMPLETE"; evaluation=None; audited=None
        if not directory.exists() or "games.json" in missing or "player_identities.json" in missing:
            status="MISSING_SNAPSHOTS"; reasons=[{"code":"MISSING_REQUIRED_SNAPSHOT","files":missing}]
        elif not quotes:
            status="MISSING_QUOTES"; reasons=[{"code":"NO_HISTORICAL_QUOTES","detail":"Paid historical quotes were not fetched automatically."}]
        elif not outcomes:
            status="MISSING_OUTCOMES"; reasons=[{"code":"NO_LOCAL_OUTCOMES"}]
        else:
            if build_missing_predictions and (overwrite_predictions or not (directory/"player_prop_predictions.json").exists()):
                build(snapshot_root,season,week,week,model_version,simulations,seed,validate=validate,overwrite=overwrite_predictions)
            predictions=_json(directory/"player_prop_predictions.json",[])
            ready=sum(r.get("readiness")=="READY" for r in predictions)
            if not ready:
                status="PREDICTIONS_NOT_READY"; reasons=[{"code":"NO_READY_PREDICTIONS"}]
            else:
                try:
                    evaluation=evaluate(snapshot_root,season,week,week,market,bookmaker,strict_outcomes)
                    audited=audit(snapshot_root,season,week,week,market=market,validate=validate)
                    if not evaluation["opportunity_rows"]:
                        status="PARTIAL"; reasons=[{"code":"NO_GRADEABLE_OPPORTUNITIES"}]
                    elif evaluation["exclusions"]:
                        status="PARTIAL"; reasons=[{"code":"RECOVERABLE_EXCLUSIONS","count":len(evaluation["exclusions"])}]
                except (ValueError, KeyError, TypeError) as exc:
                    integrity="integrity" in str(exc).lower() or "ambiguous" in str(exc).lower() or validate
                    status="INTEGRITY_FAILURE" if integrity else "RECOVERABLE_EVALUATION_ERROR"
                    reasons=[{"code":status,"detail":str(exc)[:500]}]
        # Prediction building may have changed a local input after the initial
        # resume check; persist the post-run fingerprint.
        record={"week":week,"status":status,"reasons":reasons,"missing_files":missing,
                "evaluation":evaluation,"audit":audited,
                "resume_manifest":_input_manifest(directory, config)}
        reports.append(record)
        if status not in {"COMPLETE","PARTIAL"}: failed.append({"week":week,"status":status,"reasons":reasons})
        if status in {"COMPLETE","PARTIAL"}: _write(cache,record)
        if status == "INTEGRITY_FAILURE" or (status != "COMPLETE" and not continue_on_error):
            raise RuntimeError(f"week {week}: {status}: {reasons}")

    opportunities=[r for w in reports if w.get("evaluation") for r in w["evaluation"]["opportunity_rows"]]
    exclusions=[r for w in reports if w.get("evaluation") for r in w["evaluation"]["exclusions"]]
    audit_rows=[r for w in reports if w.get("audit") for r in w["audit"]["rows"]]
    distribution_rows=[r for w in reports if w.get("audit") for r in w["audit"]["distributions"]]
    predictions=[]
    for week in range(start_week,end_week+1): predictions += _json(snapshot_week_dir(snapshot_root,"nfl",season,week)/"player_prop_predictions.json",[])
    weekly=[]; coverage=[]
    for w in reports:
        ev=w.get("evaluation"); rows=ev["opportunity_rows"] if ev else []; summary=ev["summary"] if ev else {}
        predictions_w=[r for r in predictions if int(r.get("week",-1))==w["week"]]
        ready=sum(r.get("readiness")=="READY" for r in predictions_w); not_ready=len(predictions_w)-ready
        metrics={"week":w["week"],"status":w["status"],**aggregate(rows),
                 "model_brier":probability_metrics(rows,"model_probability")["brier_score"],
                 "model_log_loss":probability_metrics(rows,"model_probability")["log_loss"],
                 "model_ece":probability_metrics(rows,"model_probability")["ece"],**{k:summary.get(k,0) for k in ("accepted_quotes","quotes_with_predictions","gradeable_quotes","unique_opportunities","gradeable_unique_opportunities","excluded_unique_opportunities","pushes","positive_edge_opportunities","positive_edge_profit","positive_edge_roi")}}
        weekly.append(metrics)
        quote_count=len(_json(snapshot_week_dir(snapshot_root,"nfl",season,w["week"])/"player_prop_odds.json",[]))
        coverage.append({"week":w["week"],"status":w["status"],"reasons":w["reasons"],"historical_quote_rows":quote_count,
                         "ready_predictions":ready,"not_ready_predictions":not_ready,"ready_coverage":ready/len(predictions_w) if predictions_w else 0,
                         "readiness_reasons":dict(sorted(Counter(r.get("readiness","UNKNOWN") for r in predictions_w).items())),
                         "outcome_identity_reconciliations":summary.get("outcome_aggregation",{}),"outcome_not_found":summary.get("outcome_not_found",0),
                         "outcome_market_missing":summary.get("outcome_market_missing",0),"prediction_missing":summary.get("exclusions_by_reason",{}).get("PREDICTION_MISSING",0),
                         "incomplete_price_pair":summary.get("incomplete_pair_quotes",0),"canonical_outcome_count":summary.get("outcome_aggregation",{}).get("canonical_outcomes",0),
                         "gradeable_opportunity_coverage_percentage":(summary.get("gradeable_unique_opportunities",0)/summary["unique_opportunities"] if summary.get("unique_opportunities") else 0)})
    market_metrics=grouped(opportunities,"market"); side_metrics=grouped(opportunities,"side"); bookmaker_metrics=grouped(opportunities,"bookmaker")
    by_week={str(w):_direction([r for r in audit_rows if int(r["week"])==w]) for w in range(start_week,end_week+1)}
    by_market={m:_direction([r for r in audit_rows if r["market"]==m]) for m in sorted({r["market"] for r in audit_rows})}
    by_side={s:_direction([r for r in audit_rows if r["side"]==s]) for s in ("OVER","UNDER")}
    direction={"global":_direction(audit_rows),"by_week":by_week,"by_market":by_market,"by_side":by_side,
               "probability_coherence_failures":sum(not x["coherent"] for w in reports if w.get("audit") for x in w["audit"]["key_diagnostics"]["coherence"]),
               "prediction_key_collisions":sum(w["audit"]["summary"]["duplicate_prediction_keys"] for w in reports if w.get("audit"))}
    distributions=[]
    for r in distribution_rows:
        summary={k:r.get(k) for k in ("minimum","maximum","mean","median","standard_deviation","unique_values","zero_mass")}
        lines=[float(x) for x in (r.get("push_mass_by_line") or {})]
        distributions.append({**r,"support_width":None if summary["minimum"] is None else summary["maximum"]-summary["minimum"],
                              "line_outside_support":any(x<summary["minimum"] or x>summary["maximum"] for x in lines) if summary["minimum"] is not None else False})
    pooled=aggregate(opportunities); model=probability_metrics(opportunities,"model_probability"); marketm=probability_metrics(opportunities,"no_vig_market_probability")
    complete_weekly=[r for r in weekly if r["opportunities"]]
    season_summary={"schema_version":1,"season":season,"weeks":[start_week,end_week],"network_contacted":False,
                    "week_status_counts":dict(sorted(Counter(w["status"] for w in reports).items())),"reused_weeks":reused,"recomputed_weeks":recomputed,
                    "micro":{**pooled,"model":model,"market":marketm,"model_minus_market":{k:(model[k]-marketm[k] if model[k] is not None and marketm[k] is not None else None) for k in ("brier_score","log_loss","ece")}},
                    "macro_weekly":{"weeks":len(complete_weekly),"model_brier":_mean(r["model_brier"] for r in complete_weekly),"model_log_loss":_mean(r["model_log_loss"] for r in complete_weekly),"roi":_mean(r["roi"] for r in complete_weekly)},
                    "clv_ready":False,"model_version":model_version}
    artifacts={"season_summary.json":season_summary,"weekly_metrics.json":weekly,"market_metrics.json":market_metrics,"side_metrics.json":side_metrics,
               "bookmaker_metrics.json":bookmaker_metrics,"calibration_by_week.json":by_week,"calibration_by_market.json":by_market,
               "roi_by_week.json":[{"week":r["week"],"roi":r["roi"],"roi_uncertainty":r["roi_uncertainty"]} for r in weekly],
               "roi_by_market.json":[{"market":r["market"],"roi":r["roi"],"roi_uncertainty":r["roi_uncertainty"]} for r in market_metrics],
               "coverage_by_week.json":coverage,"exclusions_summary.json":{"count":len(exclusions),"by_reason":dict(sorted(Counter(r["reason"] for r in exclusions).items()))},
               "readiness_summary.json":{"ready":sum(r.get("readiness")=="READY" for r in predictions),"not_ready":sum(r.get("readiness")!="READY" for r in predictions),"by_reason":dict(sorted(Counter(r.get("readiness","UNKNOWN") for r in predictions).items()))},
               "probability_direction_audit.json":direction,"distribution_diagnostics.json":{"rows":distributions},
               "systemic_findings.json":systemic_findings(direction,opportunities,coverage,distributions),"failed_weeks.json":failed}
    for name,value in artifacts.items(): _write(output_dir/name,value)
    _csv(output_dir/"weekly_metrics.csv",weekly); _csv(output_dir/"market_metrics.csv",market_metrics); _csv(output_dir/"opportunity_rows.csv",opportunities); _csv(output_dir/"exclusions.csv",exclusions)
    names=[*ARTIFACTS,"weekly_metrics.csv","market_metrics.csv","opportunity_rows.csv","exclusions.csv"]
    manifest={"schema_version":1,"network_contacted":False,"artifacts":{n:_hash(output_dir/n) for n in sorted(names)}}
    _write(output_dir/"audit_manifest.json",manifest)
    return {**artifacts,"audit_manifest.json":manifest}


def main(argv: list[str] | None = None) -> int:
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--season",type=int,required=True); p.add_argument("--start-week",type=int,default=1); p.add_argument("--end-week",type=int,default=18)
    p.add_argument("--snapshot-root",type=Path,default=SNAPSHOTS_DIR); p.add_argument("--output-dir",type=Path,required=True); p.add_argument("--model-version",choices=MODEL_VERSIONS,required=True)
    p.add_argument("--simulations",type=int,default=10000); p.add_argument("--seed",type=int,default=1729); p.add_argument("--build-missing-predictions",action="store_true"); p.add_argument("--overwrite-predictions",action="store_true")
    p.add_argument("--continue-on-error",action="store_true"); p.add_argument("--strict-outcomes",action="store_true"); p.add_argument("--market",choices=CANONICAL_PLAYER_PROP_MARKETS); p.add_argument("--bookmaker"); p.add_argument("--validate",action="store_true"); p.add_argument("--resume",action="store_true")
    args=p.parse_args(argv); run(**vars(args)); return 0


if __name__ == "__main__": raise SystemExit(main())
