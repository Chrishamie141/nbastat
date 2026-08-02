"""Nested walk-forward selection for market-residual NFL prop policies.

Every outer season/week is untouched until evaluation.  Model configuration is
chosen on prior out-of-fold probability loss; the EV threshold is then chosen
on prior out-of-fold trading results.  A non-positive inner result selects PASS.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from sklearn.metrics import roc_auc_score

from .evaluate_nfl_player_props import probability_metrics
from .evaluate_nfl_player_prop_betting_policies import (
    EV_THRESHOLDS, _base_key, _control_bets, _metrics, _model_bets,
    _paired_roi_difference, _period_key, _side_key,
    add_walk_forward_residual_probabilities,
)


def _groups(rows: list[dict[str, Any]]) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_base_key(row)].append(row)
    bad = [key for key, values in grouped.items()
           if {str(row.get("side")) for row in values} != {"OVER", "UNDER"}]
    if bad:
        raise ValueError(f"incomplete side pairs for {len(bad)} base opportunities")
    return grouped


def _market_only(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(({**row,
                    "residual_probability": float(row["no_vig_market_probability"]),
                    "residual_adjustment": 0.0,
                    "residual_model_status": "MARKET_ONLY_CONTROL"} for row in rows),
                  key=_side_key)


def _diagnostics(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    metric = probability_metrics(rows, field)
    outcomes = [1 if row["grade"] == "WIN" else 0 for row in rows]
    probabilities = [float(row[field]) for row in rows]
    auc = float(roc_auc_score(outcomes, probabilities)) if len(set(outcomes)) == 2 else None
    return {"rows": len(rows), "brier_score": metric["brier_score"],
            "log_loss": metric["log_loss"], "ece": metric["ece"], "ranking_auc": auc}


def _segments(bets: list[dict[str, Any]], *, seed: int, draws: int) -> dict[str, Any]:
    result = {}
    for field in ("market", "bookmaker"):
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in bets:
            buckets[str(row.get(field) or "UNKNOWN")].append(row)
        result[f"by_{field}"] = [{field: key,
            **_metrics(value, total_bases=len(value), seed=seed, draws=draws)}
            for key, value in sorted(buckets.items())]
    periods: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for row in bets:
        periods[_period_key(row)].append(row)
    result["by_period"] = [{"season": key[0], "week": key[1],
        **_metrics(value, total_bases=len(value), seed=seed, draws=draws)}
        for key, value in sorted(periods.items())]
    return result


def evaluate_nested_policy(
    rows: list[dict[str, Any]], *, thresholds: tuple[float, ...] = EV_THRESHOLDS,
    recency_half_lives: tuple[float | None, ...] = (None, 9.0, 18.0),
    min_model_train_rows: int = 250, min_prior_periods: int = 2,
    inner_lookback_periods: int = 8, min_inner_rows: int = 500,
    min_inner_bets: int = 100, min_inner_games: int = 10,
    roi_shrinkage_bets: int = 250, baseline_threshold: float = 0.05,
    draws: int = 2000, seed: int = 1729,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    settled = sorted(({**row} for row in rows if row.get("grade") in {"WIN", "LOSS"}),
                     key=_side_key)
    periods = sorted({_period_key(row) for row in settled})
    configurations: dict[str, list[dict[str, Any]]] = {"market_only": _market_only(settled)}
    residual_folds: dict[str, list[dict[str, Any]]] = {}
    for half_life in recency_half_lives:
        label = "residual_unweighted" if half_life is None else f"residual_half_life_{half_life:g}"
        enriched, folds = add_walk_forward_residual_probabilities(
            settled, min_train_rows=min_model_train_rows,
            min_prior_weeks=min_prior_periods,
            recency_half_life_periods=half_life, seed=seed)
        configurations[label] = enriched
        residual_folds[label] = folds

    by_config_period = {label: {period: [row for row in values if _period_key(row) == period]
                                for period in periods}
                        for label, values in configurations.items()}
    fold_reports = []
    selected_rows: list[dict[str, Any]] = []
    selected_bets: list[dict[str, Any]] = []
    v3_bets: list[dict[str, Any]] = []
    market_favorite_bets: list[dict[str, Any]] = []
    evaluated_bases = 0

    for index, period in enumerate(periods):
        prior = periods[:index]
        inner_periods = prior[-inner_lookback_periods:]
        inner_market = [row for inner in inner_periods
                        for row in by_config_period["market_only"][inner]]
        report: dict[str, Any] = {"test_season": period[0], "test_week": period[1],
            "inner_periods": [list(value) for value in inner_periods],
            "inner_rows": len(inner_market), "model_candidates": [],
            "threshold_candidates": []}
        if len(inner_market) < min_inner_rows:
            report.update({"status": "PASS_INSUFFICIENT_INNER_HISTORY",
                           "selected_configuration": None, "selected_threshold": None,
                           "test_bets": 0})
            fold_reports.append(report)
            continue

        model_candidates = []
        for label, values in configurations.items():
            inner = [row for inner_period in inner_periods
                     for row in by_config_period[label][inner_period]]
            diagnostic = _diagnostics(inner, "residual_probability")
            candidate = {"configuration": label, **diagnostic}
            model_candidates.append(candidate)
        report["model_candidates"] = model_candidates
        selected_model = min(model_candidates,
                             key=lambda value: (float(value["log_loss"]), value["configuration"]))
        label = str(selected_model["configuration"])
        inner = [row for inner_period in inner_periods
                 for row in by_config_period[label][inner_period]]
        inner_groups = _groups(inner)
        threshold_candidates = []
        for threshold in sorted(set(float(value) for value in thresholds)):
            bets = _model_bets(inner_groups, "residual_probability", threshold)
            metrics = _metrics(bets, total_bases=len(inner_groups), seed=seed, draws=0)
            eligible = (len(bets) >= min_inner_bets and
                        metrics["independent_games"] >= min_inner_games)
            score = (float(metrics["units_profit"]) /
                     (len(bets) + roi_shrinkage_bets)) if eligible else None
            threshold_candidates.append({"threshold": threshold, "eligible": eligible,
                                         "selection_score": score, **metrics})
        report["threshold_candidates"] = threshold_candidates
        profitable = [value for value in threshold_candidates
                      if value["eligible"] and value["selection_score"] is not None
                      and value["selection_score"] > 0]
        chosen = max(profitable, key=lambda value: (value["selection_score"],
                     value["roi"], value["threshold"])) if profitable else None

        test = by_config_period[label][period]
        test_groups = _groups(test)
        selected_rows.extend(test)
        evaluated_bases += len(test_groups)
        fold_bets = [] if chosen is None else _model_bets(
            test_groups, "residual_probability", float(chosen["threshold"]))
        for row in fold_bets:
            row["nested_configuration"] = label
            row["nested_threshold"] = float(chosen["threshold"])
        selected_bets.extend(fold_bets)
        v3_bets.extend(_model_bets(test_groups, "baseline_probability", baseline_threshold))
        market_favorite_bets.extend(_control_bets(test_groups, "market_favorite", seed))
        report.update({"status": "BET" if fold_bets else "PASS_NO_POSITIVE_INNER_POLICY",
                       "selected_configuration": label,
                       "selected_threshold": None if chosen is None else chosen["threshold"],
                       "test_base_opportunities": len(test_groups), "test_bets": len(fold_bets)})
        fold_reports.append(report)

    candidate_metrics = _metrics(selected_bets, total_bases=evaluated_bases, seed=seed, draws=draws)
    v3_metrics = _metrics(v3_bets, total_bases=evaluated_bases, seed=seed, draws=draws)
    favorite_metrics = _metrics(market_favorite_bets, total_bases=evaluated_bases, seed=seed, draws=draws)
    market_probability = _diagnostics(selected_rows, "no_vig_market_probability")
    nested_probability = _diagnostics(selected_rows, "residual_probability")
    probability_gates = {
        "brier_no_worse_than_market": nested_probability["brier_score"] <= market_probability["brier_score"],
        "log_loss_no_worse_than_market": nested_probability["log_loss"] <= market_probability["log_loss"],
        "ranking_auc_above_market": nested_probability["ranking_auc"] > market_probability["ranking_auc"],
        "ece_at_most_two_percent": nested_probability["ece"] <= 0.02,
    }
    v3_comparison = _paired_roi_difference(selected_bets, v3_bets, seed=seed, draws=draws)
    favorite_comparison = _paired_roi_difference(
        selected_bets, market_favorite_bets, seed=seed, draws=draws)
    periods_bet = {_period_key(row) for row in selected_bets}
    markets_bet = {str(row.get("market")) for row in selected_bets}
    books_bet = {str(row.get("bookmaker")) for row in selected_bets}
    sufficient = {"at_least_500_bets": len(selected_bets) >= 500,
                  "at_least_50_games": candidate_metrics["independent_games"] >= 50,
                  "at_least_8_periods": len(periods_bet) >= 8,
                  "at_least_3_markets": len(markets_bet) >= 3,
                  "at_least_3_books": len(books_bet) >= 3}
    absolute = candidate_metrics.get("roi_ci_95")
    trading_gates = {
        "positive_absolute_roi_lower_bound": bool(absolute and absolute[0] > 0),
        "positive_roi_improvement_lower_bound_vs_v3": bool(
            v3_comparison["ci_95"] and v3_comparison["ci_95"][0] > 0),
        "positive_roi_improvement_lower_bound_vs_market_favorite": bool(
            favorite_comparison["ci_95"] and favorite_comparison["ci_95"][0] > 0),
    }
    duplicate_bets = len(selected_bets) - len({_base_key(row) for row in selected_bets})
    report = {"schema_version": 1, "evaluation_role": "NESTED_HISTORICAL_VALIDATION_ONLY",
        "selection_allowed": False, "one_side_per_base_contract": duplicate_bets == 0,
        "duplicate_base_bets": duplicate_bets,
        "evaluation_periods": [list(value) for value in periods],
        "evaluated_base_opportunities": evaluated_bases,
        "configuration_candidates": list(configurations),
        "recency_half_lives": list(recency_half_lives), "threshold_candidates": list(thresholds),
        "controls": {"inner_lookback_periods": inner_lookback_periods,
            "min_inner_rows": min_inner_rows, "min_inner_bets": min_inner_bets,
            "min_inner_games": min_inner_games, "roi_shrinkage_bets": roi_shrinkage_bets,
            "baseline_threshold": baseline_threshold, "seed": seed, "bootstrap_draws": draws},
        "folds": fold_reports, "residual_model_folds": residual_folds,
        "selected_configuration_counts": dict(sorted(Counter(
            value.get("selected_configuration") for value in fold_reports
            if value.get("selected_configuration")).items())),
        "selected_threshold_counts": dict(sorted(Counter(
            str(value.get("selected_threshold")) for value in fold_reports
            if value.get("selected_threshold") is not None).items())),
        "probability_diagnostics": {"market_no_vig": market_probability,
                                    "nested_selected_residual": nested_probability},
        "policy_metrics": {"nested_residual": candidate_metrics,
                           "v3_fixed_threshold": v3_metrics,
                           "market_favorite": favorite_metrics},
        "policy_comparisons": {"nested_vs_v3": v3_comparison,
                               "nested_vs_market_favorite": favorite_comparison},
        "probability_gates": probability_gates, "trading_gates": trading_gates,
        "sample_sufficiency_gates": sufficient,
        "promotion_eligible": (all(probability_gates.values()) and
                               all(trading_gates.values()) and all(sufficient.values())),
        "segment_metrics": _segments(selected_bets, seed=seed, draws=draws),
        "guardrails": ["Every outer season/week is untouched until its test fold.",
            "Configuration selection uses prior out-of-fold log loss only.",
            "Threshold selection uses prior out-of-fold returns only and may choose PASS.",
            "Historical results cannot authorize production wagering; forward shadow evidence is required."]}
    return report, sorted(selected_bets, key=_side_key)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
                    encoding="utf-8")


def write_outputs(report: dict[str, Any], bets: list[dict[str, Any]], output_dir: Path,
                  inputs: list[Path]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "nested_policy_evaluation.json", report)
    _write_json(output_dir / "nested_policy_folds.json", report["folds"])
    fields = sorted({field for row in bets for field in row
                     if not isinstance(row[field], (dict, list))})
    with (output_dir / "nested_policy_bets.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(bets)
    _write_json(output_dir / "nested_policy_manifest.json", {
        "schema_version": 1, "inputs": [{"path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in inputs],
        "network_contacted": False, "promotion_eligible": report["promotion_eligible"]})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, nargs="+", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--thresholds", default=",".join(str(value) for value in EV_THRESHOLDS))
    parser.add_argument("--recency-half-lives", default="none,9,18")
    parser.add_argument("--min-model-train-rows", type=int, default=250)
    parser.add_argument("--min-prior-periods", type=int, default=2)
    parser.add_argument("--inner-lookback-periods", type=int, default=8)
    parser.add_argument("--min-inner-rows", type=int, default=500)
    parser.add_argument("--min-inner-bets", type=int, default=100)
    parser.add_argument("--min-inner-games", type=int, default=10)
    parser.add_argument("--roi-shrinkage-bets", type=int, default=250)
    parser.add_argument("--baseline-threshold", type=float, default=0.05)
    parser.add_argument("--bootstrap-draws", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=1729)
    args = parser.parse_args(argv)
    rows = []
    for path in args.predictions:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, list):
            raise ValueError(f"prediction input must contain a JSON array: {path}")
        rows.extend(value)
    thresholds = tuple(float(value) for value in args.thresholds.split(","))
    half_lives = tuple(None if value.strip().casefold() == "none" else float(value)
                       for value in args.recency_half_lives.split(","))
    report, bets = evaluate_nested_policy(rows, thresholds=thresholds,
        recency_half_lives=half_lives, min_model_train_rows=args.min_model_train_rows,
        min_prior_periods=args.min_prior_periods,
        inner_lookback_periods=args.inner_lookback_periods,
        min_inner_rows=args.min_inner_rows, min_inner_bets=args.min_inner_bets,
        min_inner_games=args.min_inner_games, roi_shrinkage_bets=args.roi_shrinkage_bets,
        baseline_threshold=args.baseline_threshold, draws=args.bootstrap_draws, seed=args.seed)
    write_outputs(report, bets, args.output_dir, args.predictions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
