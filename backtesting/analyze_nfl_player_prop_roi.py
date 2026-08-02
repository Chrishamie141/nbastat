"""Offline, descriptive ROI diagnostics for paired NFL player-prop forecasts.

This module diagnoses where a frozen evaluation won or lost money. It does not
select a threshold or tune a model on the evaluation season. Any policy change
identified here must be preregistered and validated on a later forward sample.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


DEFAULT_THRESHOLDS = (0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.075, 0.10, 0.15, 0.20)
POLICIES = {
    "v3_baseline": ("baseline_probability", "baseline_edge"),
    "v4_candidate": ("model_probability", "candidate_edge"),
}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({field for row in rows for field in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _settled(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows
            if str(row.get("grade") or "").upper() in {"WIN", "LOSS"}
            and isinstance(row.get("profit_units"), (int, float))]


def _selected(rows: Iterable[dict[str, Any]], edge_field: str, threshold: float) -> list[dict[str, Any]]:
    return [row for row in _settled(rows)
            if isinstance(row.get(edge_field), (int, float)) and float(row[edge_field]) >= threshold]


def _roi(rows: list[dict[str, Any]]) -> float | None:
    return sum(float(row["profit_units"]) for row in rows) / len(rows) if rows else None


def _cluster_interval(rows: list[dict[str, Any]], *, seed: int, draws: int) -> dict[str, Any]:
    games: dict[str, tuple[float, int]] = {}
    for row in rows:
        key = str(row.get("game_id") or "")
        profit, count = games.get(key, (0.0, 0))
        games[key] = (profit + float(row["profit_units"]), count + 1)
    keys = sorted(games)
    if len(keys) < 2 or draws <= 0:
        return {"method": "deterministic_game_cluster_bootstrap", "draws": draws,
                "independent_games": len(keys), "ci_95": None}
    rng = random.Random(seed)
    values = []
    for _ in range(draws):
        sampled = [games[rng.choice(keys)] for _key in keys]
        profit = sum(value[0] for value in sampled)
        count = sum(value[1] for value in sampled)
        if count:
            values.append(profit / count)
    values.sort()
    lower = values[int(0.025 * len(values))]
    upper = values[max(0, int(0.975 * len(values)) - 1)]
    return {"method": "deterministic_game_cluster_bootstrap", "draws": draws,
            "independent_games": len(keys), "ci_95": [lower, upper]}


def _metric(rows: list[dict[str, Any]], *, seed: int, draws: int) -> dict[str, Any]:
    profit = sum(float(row["profit_units"]) for row in rows)
    wins = sum(str(row.get("grade")).upper() == "WIN" for row in rows)
    interval = _cluster_interval(rows, seed=seed, draws=draws)
    return {"bets": len(rows), "wins": wins, "losses": len(rows) - wins,
            "win_rate": wins / len(rows) if rows else None,
            "units_profit": profit, "roi": profit / len(rows) if rows else None,
            "independent_games": interval["independent_games"], "roi_ci_95": interval["ci_95"],
            "bootstrap_method": interval["method"], "bootstrap_draws": interval["draws"]}


def _group(rows: list[dict[str, Any]], fields: tuple[str, ...], *, seed: int, draws: int) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(str(row.get(field) if row.get(field) is not None else "UNKNOWN") for field in fields)].append(row)
    return [{**{field: key[index] for index, field in enumerate(fields)},
             **_metric(groups[key], seed=seed, draws=draws)} for key in sorted(groups)]


def _leave_one_out(rows: list[dict[str, Any]], field: str, *, seed: int, draws: int) -> list[dict[str, Any]]:
    values = sorted({str(row.get(field) if row.get(field) is not None else "UNKNOWN") for row in rows})
    result = []
    for value in values:
        kept = [row for row in rows if str(row.get(field) if row.get(field) is not None else "UNKNOWN") != value]
        result.append({f"excluded_{field}": value, **_metric(kept, seed=seed, draws=draws)})
    return result


def analyze(rows: list[dict[str, Any]], *, thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS,
            decision_threshold: float = 0.05, seed: int = 1729, draws: int = 1000) -> dict[str, Any]:
    settled = _settled(rows)
    threshold_rows: list[dict[str, Any]] = []
    segments: dict[str, Any] = {}
    stability: dict[str, Any] = {}
    for policy, (_probability_field, edge_field) in POLICIES.items():
        for threshold in thresholds:
            chosen = _selected(settled, edge_field, threshold)
            threshold_rows.append({"policy": policy, "edge_threshold": threshold,
                                   **_metric(chosen, seed=seed, draws=draws),
                                   "evidence_role": "DESCRIPTIVE_ONLY_NOT_FOR_SELECTION"})
        chosen = _selected(settled, edge_field, decision_threshold)
        segments[policy] = {
            "decision_threshold": decision_threshold,
            "by_market": _group(chosen, ("market",), seed=seed, draws=draws),
            "by_bookmaker": _group(chosen, ("bookmaker",), seed=seed, draws=draws),
            "by_week": _group(chosen, ("week",), seed=seed, draws=draws),
            "by_side": _group(chosen, ("side",), seed=seed, draws=draws),
            "by_market_bookmaker": _group(chosen, ("market", "bookmaker"), seed=seed, draws=draws),
        }
        stability[policy] = {
            "leave_one_week_out": _leave_one_out(chosen, "week", seed=seed, draws=draws),
            "leave_one_market_out": _leave_one_out(chosen, "market", seed=seed, draws=draws),
            "leave_one_bookmaker_out": _leave_one_out(chosen, "bookmaker", seed=seed, draws=draws),
        }
    positive_intervals = [row for row in threshold_rows
                          if row["roi_ci_95"] is not None and row["roi_ci_95"][0] > 0]
    return {
        "schema_version": 1,
        "analysis_role": "DESCRIPTIVE_DIAGNOSTIC_ONLY",
        "selection_allowed": False,
        "evaluation_rows": len(rows),
        "settled_rows": len(settled),
        "weeks": sorted({int(row["week"]) for row in settled}),
        "independent_games": len({str(row.get("game_id") or "") for row in settled}),
        "thresholds": list(thresholds),
        "decision_threshold": decision_threshold,
        "threshold_metrics": threshold_rows,
        "segments": segments,
        "stability": stability,
        "descriptive_positive_ci_thresholds": positive_intervals,
        "guardrails": [
            "Thresholds are a preregistered diagnostic grid, not a search result.",
            "No threshold or segment discovered on 2025 may be promoted from this analysis.",
            "Any policy change must be frozen before a later forward shadow sample.",
            "Confidence intervals resample independent games, not correlated side rows.",
        ],
        "required_next_evidence": "A later forward shadow season or other untouched out-of-sample window.",
    }


def write_outputs(report: dict[str, Any], output_dir: Path, input_path: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "roi_diagnostics.json", report)
    _write_csv(output_dir / "roi_by_edge_threshold.csv", report["threshold_metrics"])
    segment_rows = []
    for policy, groups in report["segments"].items():
        for group_name, values in groups.items():
            if group_name == "decision_threshold":
                continue
            segment_rows.extend({"policy": policy, "segment": group_name, **row} for row in values)
    _write_csv(output_dir / "roi_segments.csv", segment_rows)
    artifacts = [output_dir / name for name in ("roi_diagnostics.json", "roi_by_edge_threshold.csv", "roi_segments.csv")]
    _write_json(output_dir / "roi_diagnostics_manifest.json", {
        "schema_version": 1, "network_contacted": False,
        "input": {input_path.as_posix(): _hash(input_path)},
        "artifacts": {path.name: _hash(path) for path in artifacts},
    })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--decision-threshold", type=float, default=0.05)
    parser.add_argument("--bootstrap-draws", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=1729)
    args = parser.parse_args(argv)
    rows = json.loads(args.predictions.read_text(encoding="utf-8"))
    report = analyze(rows, decision_threshold=args.decision_threshold,
                     seed=args.seed, draws=args.bootstrap_draws)
    write_outputs(report, args.output_dir, args.predictions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
