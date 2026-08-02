"""Evaluate realistic one-side-or-no-bet NFL player-prop policies offline.

The primary unit is one player/game/market/line. Every policy may select at
most one side for that unit. Market-residual probabilities are fit strictly on
earlier evaluation weeks and remain validation-only until a later forward
shadow sample confirms them.
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
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

from .evaluate_nfl_player_props import probability_metrics


EV_THRESHOLDS = (0.0, 0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15, 0.20)
MODEL_POLICIES = {
    "market_only_ev": "no_vig_market_probability",
    "v3_ev": "baseline_probability",
    "v4_ev": "model_probability",
    "market_residual_walk_forward_ev": "residual_probability",
}


def _base_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (row.get("season"), row.get("week"), row.get("game_id"),
            row.get("canonical_player_id"), row.get("market"), float(row.get("line")))


def _side_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (*_base_key(row), str(row.get("side") or ""), str(row.get("bookmaker") or ""))


def _period_key(row: dict[str, Any]) -> tuple[int, int]:
    """Return the chronological season/week key used by every walk-forward split."""
    return (int(row["season"]), int(row["week"]))


def _expected_value(row: dict[str, Any], probability_field: str) -> float | None:
    probability, odds = row.get(probability_field), row.get("decimal_odds")
    if not isinstance(probability, (int, float)) or not isinstance(odds, (int, float)) or odds <= 1:
        return None
    push = float(row.get("push_probability") or 0)
    loss = max(0.0, 1 - float(probability) - push)
    return float(probability) * (float(odds) - 1) - loss


def _logit(value: float) -> float:
    clipped = min(1 - 1e-6, max(1e-6, value))
    return math.log(clipped / (1 - clipped))


def add_walk_forward_residual_probabilities(rows: list[dict[str, Any]], *,
                                            min_train_rows: int = 100,
                                            min_prior_weeks: int = 2,
                                            recency_half_life_periods: float | None = None,
                                            seed: int = 1729) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fit market probability plus a learned V4 residual on earlier periods only.

    A period is one season/week.  This distinction is essential when multiple
    seasons are supplied: Week 1 of a later season must train on the preceding
    season, never on Week 2+ of its own season.  Optional exponential weights
    decay by chronological period and do not change the default behavior.
    """
    output = [{**row} for row in rows]
    folds = []
    markets = sorted({str(row.get("market") or "") for row in output})
    periods = sorted({_period_key(row) for row in output})
    period_index = {period: index for index, period in enumerate(periods)}
    for market in markets:
        market_rows = [row for row in output if str(row.get("market") or "") == market]
        for period in periods:
            train = [row for row in market_rows if _period_key(row) < period and row.get("grade") in {"WIN", "LOSS"}]
            test = [row for row in market_rows if _period_key(row) == period]
            prior_periods = sorted({_period_key(row) for row in train})
            ready = (len(train) >= min_train_rows and len(prior_periods) >= min_prior_weeks
                     and len({row["grade"] for row in train}) == 2)
            model = None
            sample_weight = None
            if ready:
                xtrain = np.asarray([[
                    _logit(float(row["no_vig_market_probability"])),
                    float(row["model_probability"]) - float(row["no_vig_market_probability"]),
                ] for row in train])
                ytrain = np.asarray([row["grade"] == "WIN" for row in train], dtype=int)
                if recency_half_life_periods is not None:
                    if recency_half_life_periods <= 0:
                        raise ValueError("recency_half_life_periods must be positive")
                    test_index = period_index[period]
                    sample_weight = np.asarray([
                        0.5 ** ((test_index - period_index[_period_key(row)]) /
                                float(recency_half_life_periods)) for row in train
                    ])
                model = LogisticRegression(C=1.0, max_iter=2000, random_state=seed).fit(
                    xtrain, ytrain, sample_weight=sample_weight)
            raw_by_base: dict[tuple[Any, ...], list[tuple[dict[str, Any], float]]] = defaultdict(list)
            for row in test:
                market_probability = float(row["no_vig_market_probability"])
                if model is None:
                    raw = market_probability
                    status = "MARKET_ONLY_INSUFFICIENT_PRIOR_HISTORY"
                else:
                    vector = np.asarray([[
                        _logit(market_probability),
                        float(row["model_probability"]) - market_probability,
                    ]])
                    raw = float(model.predict_proba(vector)[0, 1])
                    status = "READY_WALK_FORWARD"
                row["residual_model_status"] = status
                raw_by_base[_base_key(row)].append((row, raw))
            for values in raw_by_base.values():
                total = sum(value for _row, value in values)
                push = max(float(row.get("push_probability") or 0) for row, _value in values)
                available = max(0.0, 1 - push)
                for row, raw in values:
                    probability = available * raw / total if total else available / len(values)
                    row["residual_probability"] = probability
                    row["residual_adjustment"] = probability - float(row["no_vig_market_probability"])
            folds.append({"market": market, "test_season": period[0], "test_week": period[1],
                          "train_rows": len(train), "train_weeks": sorted({p[1] for p in prior_periods}),
                          "train_periods": [list(value) for value in prior_periods], "test_rows": len(test),
                          "status": "READY_WALK_FORWARD" if model is not None else "MARKET_ONLY_INSUFFICIENT_PRIOR_HISTORY",
                          "recency_half_life_periods": recency_half_life_periods,
                          "effective_train_weight": None if sample_weight is None else float(sample_weight.sum()),
                          "market_logit_coefficient": None if model is None else float(model.coef_[0][0]),
                          "v4_residual_coefficient": None if model is None else float(model.coef_[0][1]),
                          "intercept": None if model is None else float(model.intercept_[0])})
    return sorted(output, key=_side_key), folds


def _cluster_interval(bets: list[dict[str, Any]], *, seed: int, draws: int) -> list[float] | None:
    games: dict[str, tuple[float, int]] = {}
    for row in bets:
        key = str(row.get("game_id") or "")
        profit, count = games.get(key, (0.0, 0))
        games[key] = (profit + float(row["profit_units"]), count + 1)
    keys = sorted(games)
    if len(keys) < 2 or draws <= 0:
        return None
    rng = random.Random(seed); values = []
    for _ in range(draws):
        sampled = [games[rng.choice(keys)] for _key in keys]
        profit = sum(value[0] for value in sampled); count = sum(value[1] for value in sampled)
        values.append(profit / count)
    values.sort()
    return [values[int(0.025 * len(values))], values[max(0, int(0.975 * len(values)) - 1)]]


def _paired_roi_difference(candidate: list[dict[str, Any]], baseline: list[dict[str, Any]], *,
                           seed: int, draws: int) -> dict[str, Any]:
    def grouped(rows: list[dict[str, Any]]) -> dict[str, tuple[float, int]]:
        result: dict[str, tuple[float, int]] = {}
        for row in rows:
            key = str(row.get("game_id") or "")
            profit, count = result.get(key, (0.0, 0))
            result[key] = (profit + float(row["profit_units"]), count + 1)
        return result
    candidate_games, baseline_games = grouped(candidate), grouped(baseline)
    keys = sorted(set(candidate_games) | set(baseline_games))
    estimate = (_metrics(candidate, total_bases=1, seed=seed, draws=0)["roi"] or 0.0) - \
        (_metrics(baseline, total_bases=1, seed=seed, draws=0)["roi"] or 0.0)
    if len(keys) < 2 or draws <= 0:
        interval = None
    else:
        rng = random.Random(seed); values = []
        for _ in range(draws):
            sampled = [rng.choice(keys) for _key in keys]
            candidate_profit = sum(candidate_games.get(key, (0.0, 0))[0] for key in sampled)
            candidate_count = sum(candidate_games.get(key, (0.0, 0))[1] for key in sampled)
            baseline_profit = sum(baseline_games.get(key, (0.0, 0))[0] for key in sampled)
            baseline_count = sum(baseline_games.get(key, (0.0, 0))[1] for key in sampled)
            candidate_roi = candidate_profit / candidate_count if candidate_count else 0.0
            baseline_roi = baseline_profit / baseline_count if baseline_count else 0.0
            values.append(candidate_roi - baseline_roi)
        values.sort()
        interval = [values[int(0.025 * len(values))], values[max(0, int(0.975 * len(values)) - 1)]]
    return {"estimate": estimate, "ci_95": interval,
            "method": "paired_deterministic_game_cluster_bootstrap", "draws": draws}


def _max_drawdown(bets: list[dict[str, Any]]) -> float:
    ordered = sorted(bets, key=lambda row: (_period_key(row), str(row.get("game_id")), _side_key(row)))
    equity = peak = drawdown = 0.0
    for row in ordered:
        equity += float(row["profit_units"]); peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _metrics(bets: list[dict[str, Any]], *, total_bases: int, seed: int, draws: int) -> dict[str, Any]:
    wins = sum(row.get("grade") == "WIN" for row in bets)
    profit = sum(float(row["profit_units"]) for row in bets)
    return {"base_opportunities": total_bases, "bets": len(bets),
            "bet_rate": len(bets) / total_bases if total_bases else None,
            "wins": wins, "losses": len(bets) - wins,
            "hit_rate": wins / len(bets) if bets else None,
            "units_profit": profit, "roi": profit / len(bets) if bets else None,
            "roi_ci_95": _cluster_interval(bets, seed=seed, draws=draws),
            "max_drawdown_units": _max_drawdown(bets),
            "independent_games": len({str(row.get("game_id")) for row in bets})}


def _model_bets(groups: dict[tuple[Any, ...], list[dict[str, Any]]], probability_field: str,
                threshold: float) -> list[dict[str, Any]]:
    bets = []
    for key in sorted(groups):
        candidates = []
        for row in groups[key]:
            ev = _expected_value(row, probability_field)
            if ev is not None:
                candidates.append((ev, str(row.get("side")), row))
        if not candidates:
            continue
        ev, _side, row = max(candidates, key=lambda value: (value[0], value[1]))
        if ev >= threshold:
            bets.append({**row, "policy_probability": row[probability_field], "policy_expected_value": ev})
    return bets


def _fixed_side(groups: dict[tuple[Any, ...], list[dict[str, Any]]], side: str) -> list[dict[str, Any]]:
    return [{**next(row for row in groups[key] if row.get("side") == side), "policy_expected_value": None}
            for key in sorted(groups) if any(row.get("side") == side for row in groups[key])]


def _control_bets(groups: dict[tuple[Any, ...], list[dict[str, Any]]], policy: str, seed: int) -> list[dict[str, Any]]:
    if policy == "always_over": return _fixed_side(groups, "OVER")
    if policy == "always_under": return _fixed_side(groups, "UNDER")
    bets = []
    for key in sorted(groups):
        rows = groups[key]
        if policy in {"market_favorite", "highest_no_vig_probability"}:
            row = max(rows, key=lambda value: (float(value["no_vig_market_probability"]), str(value.get("side"))))
        elif policy == "deterministic_random_side":
            digest = hashlib.sha256((json.dumps(key, sort_keys=True) + f"|{seed}").encode()).digest()
            side = "OVER" if digest[0] % 2 == 0 else "UNDER"
            row = next(value for value in rows if value.get("side") == side)
        else:
            raise ValueError(f"unknown control policy: {policy}")
        bets.append({**row, "policy_probability": row.get("no_vig_market_probability"), "policy_expected_value": None})
    return bets


def evaluate_policies(rows: list[dict[str, Any]], *, decision_threshold: float = 0.05,
                      thresholds: tuple[float, ...] = EV_THRESHOLDS,
                      min_train_rows: int = 100, seed: int = 1729,
                      draws: int = 1000) -> dict[str, Any]:
    settled = [row for row in rows if row.get("grade") in {"WIN", "LOSS"}]
    evaluation_seasons = sorted({int(row["season"]) for row in settled
                                 if row.get("season") is not None})
    season_label = ", ".join(str(season) for season in evaluation_seasons) or "the supplied history"
    enriched, folds = add_walk_forward_residual_probabilities(
        settled, min_train_rows=min_train_rows, seed=seed)
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in enriched: groups[_base_key(row)].append(row)
    incoherent = [list(key) for key, values in groups.items()
                  if {row.get("side") for row in values} != {"OVER", "UNDER"}]
    if incoherent:
        raise ValueError(f"incomplete side pairs for {len(incoherent)} base opportunities")
    total_bases = len(groups)
    probability_diagnostics = {}
    for policy, field in {
        "market_no_vig": "no_vig_market_probability",
        "v3_baseline": "baseline_probability",
        "v4_candidate": "model_probability",
        "market_residual_walk_forward": "residual_probability",
    }.items():
        metric = probability_metrics(enriched, field)
        outcomes = [1 if row["grade"] == "WIN" else 0 for row in enriched]
        probabilities = [float(row[field]) for row in enriched]
        probability_diagnostics[policy] = {
            "rows": len(enriched), "brier_score": metric["brier_score"],
            "log_loss": metric["log_loss"], "ece": metric["ece"],
            "ranking_auc": float(roc_auc_score(outcomes, probabilities)),
        }
    controls = {}
    for policy in ("always_over", "always_under", "deterministic_random_side",
                   "market_favorite", "highest_no_vig_probability"):
        bets = _control_bets(groups, policy, seed)
        controls[policy] = _metrics(bets, total_bases=total_bases, seed=seed, draws=draws)
    controls["no_bet"] = _metrics([], total_bases=total_bases, seed=seed, draws=draws)
    threshold_metrics = []
    selected: dict[str, Any] = {}
    selected_bets: dict[str, list[dict[str, Any]]] = {}
    segment_metrics: dict[str, Any] = {}
    for policy, probability_field in MODEL_POLICIES.items():
        for threshold in thresholds:
            bets = _model_bets(groups, probability_field, threshold)
            threshold_metrics.append({"policy": policy, "expected_value_threshold": threshold,
                                      **_metrics(bets, total_bases=total_bases, seed=seed, draws=draws)})
        bets = _model_bets(groups, probability_field, decision_threshold)
        selected_bets[policy] = bets
        selected[policy] = _metrics(bets, total_bases=total_bases, seed=seed, draws=draws)
        by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_week: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_book: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in bets:
            by_market[str(row.get("market"))].append(row)
            by_week[str(row.get("week"))].append(row)
            by_book[str(row.get("bookmaker"))].append(row)
        segment_metrics[policy] = {
            "by_market": [{"market": key, **_metrics(value, total_bases=len(value), seed=seed, draws=draws)} for key, value in sorted(by_market.items())],
            "by_week": [{"week": key, **_metrics(value, total_bases=len(value), seed=seed, draws=draws)} for key, value in sorted(by_week.items(), key=lambda item: int(item[0]))],
            "by_bookmaker": [{"bookmaker": key, **_metrics(value, total_bases=len(value), seed=seed, draws=draws)} for key, value in sorted(by_book.items())],
        }
    comparisons = {}
    baseline_bets = selected_bets["v3_ev"]
    for policy in ("v4_ev", "market_residual_walk_forward_ev"):
        absolute = selected[policy].get("roi_ci_95")
        improvement = _paired_roi_difference(selected_bets[policy], baseline_bets, seed=seed, draws=draws)
        comparisons[f"{policy}_vs_v3_ev"] = {
            "roi_improvement": improvement,
            "roi_improvement_gate_pass": bool(improvement["ci_95"] and improvement["ci_95"][0] > 0),
            "absolute_positive_roi_gate_pass": bool(absolute and absolute[0] > 0),
            "promotion_eligible_on_roi": bool(improvement["ci_95"] and improvement["ci_95"][0] > 0
                                                and absolute and absolute[0] > 0),
        }
    return {"schema_version": 1, "evaluation_role": "HISTORICAL_VALIDATION_ONLY",
            "evaluation_seasons": evaluation_seasons,
            "selection_allowed": False, "one_side_per_base_contract": True,
            "base_key_fields": ["season", "week", "game_id", "canonical_player_id", "market", "line"],
            "base_opportunities": total_bases, "side_rows": len(enriched),
            "decision_threshold": decision_threshold, "decision_threshold_unit": "expected_profit_per_unit_staked",
            "controls": controls, "selected_policy_metrics": selected,
            "policy_comparisons": comparisons,
            "probability_diagnostics": probability_diagnostics,
            "threshold_metrics": threshold_metrics, "segment_metrics": segment_metrics,
            "residual_model_folds": folds,
            "guardrails": [
                "Every policy selects at most one side per base opportunity.",
                "The residual model trains only on earlier weeks and uses market probability as its fallback.",
                f"{season_label} has already been observed and is validation-only; no discovered threshold is promotion evidence.",
                "Promotion requires an untouched forward shadow window whose ROI lower confidence bound exceeds zero.",
            ]}


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({field for row in rows for field in row if not isinstance(row[field], (dict, list))})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)


def write_outputs(report: dict[str, Any], output_dir: Path, input_path: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "betting_policy_evaluation.json"
    threshold_path = output_dir / "betting_policy_thresholds.csv"
    _write(report_path, report); _csv(threshold_path, report["threshold_metrics"])
    _write(output_dir / "betting_policy_manifest.json", {
        "schema_version": 1, "network_contacted": False,
        "input": {input_path.as_posix(): hashlib.sha256(input_path.read_bytes()).hexdigest()},
        "artifacts": {report_path.name: hashlib.sha256(report_path.read_bytes()).hexdigest(),
                      threshold_path.name: hashlib.sha256(threshold_path.read_bytes()).hexdigest()},
    })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--decision-threshold", type=float, default=0.05)
    parser.add_argument("--min-train-rows", type=int, default=100)
    parser.add_argument("--bootstrap-draws", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=1729)
    args = parser.parse_args(argv)
    rows = json.loads(args.predictions.read_text(encoding="utf-8"))
    report = evaluate_policies(rows, decision_threshold=args.decision_threshold,
                               min_train_rows=args.min_train_rows,
                               seed=args.seed, draws=args.bootstrap_draws)
    write_outputs(report, args.output_dir, args.predictions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
