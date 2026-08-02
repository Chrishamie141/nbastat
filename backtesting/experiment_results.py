"""Shared, deterministic experiment-result contract for model benchmarking."""
from __future__ import annotations

import math
import random
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from .evaluate_nfl_player_props import probability_metrics
from .model_registry import SCHEMA_VERSION, content_hash, file_hash, git_commit


def _metric(rows: list[dict[str, Any]]) -> dict[str, Any]:
    probability = probability_metrics(rows, "model_probability")
    decisions_present = any(row.get("decision") is not None for row in rows)
    wagered = [row for row in rows if not decisions_present or str(row.get("decision")).upper() == "BET"]
    settled = [row for row in wagered if str(row.get("grade")).upper() in {"WIN", "LOSS"}]
    profit = sum(float(row.get("profit_units") or 0) for row in settled)
    return {
        "opportunities": len(rows),
        "bets": len(wagered),
        "settled": len(settled),
        "brier_score": probability["brier_score"],
        "log_loss": probability["log_loss"],
        "ece": probability["ece"],
        "roi": profit / len(settled) if settled else None,
        "units_profit": profit,
    }


def _segments(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(field) or "UNKNOWN")].append(row)
    return {key: _metric(groups[key]) for key in sorted(groups)}


def _roi_uncertainty(rows: list[dict[str, Any]], seed: int, draws: int = 2000) -> dict[str, Any]:
    settled = [row for row in rows if str(row.get("grade")).upper() in {"WIN", "LOSS"}]
    games: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in settled:
        games[str(row.get("game_id") or "UNKNOWN")].append(row)
    keys = sorted(games)
    if len(keys) < 2:
        return {"method": "game_cluster_bootstrap", "clusters": len(keys),
                "draws": draws, "standard_error": None, "confidence_interval_95": None}
    rng = random.Random(seed)
    estimates = []
    for _ in range(draws):
        sample = [row for _key in keys for row in games[rng.choice(keys)]]
        estimates.append(sum(float(row.get("profit_units") or 0) for row in sample) / len(sample))
    mean = sum(estimates) / len(estimates)
    variance = sum((value - mean) ** 2 for value in estimates) / (len(estimates) - 1)
    standard_error = math.sqrt(variance)
    ordered = sorted(estimates)
    return {"method": "deterministic_game_cluster_bootstrap", "clusters": len(keys), "draws": draws,
            "standard_error": standard_error,
            "confidence_interval_95": [ordered[int(.025 * draws)], ordered[int(.975 * draws) - 1]]}


def _artifact_records(paths: Iterable[Path]) -> dict[str, Any]:
    # Artifact names and content hashes are portable across output directories.
    # The adjacent research manifest owns location-specific verification.
    return {path.name: {"path": path.name, "sha256": file_hash(path)}
            for path in sorted(paths, key=lambda item: item.name)}


def write_reliability_svg(path: Path, bins: list[dict[str, Any]]) -> None:
    """Write a dependency-free deterministic reliability plot."""
    width, height, left, top, plot = 640, 480, 80, 40, 360
    points = sorted((float(item["predicted_probability_mean"]), float(item["observed_win_rate"]),
                     int(item["count"])) for item in bins if item.get("count"))
    coordinates = [(left + x * plot, top + (1 - y) * plot, count) for x, y, count in points]
    polyline = " ".join(f"{x:.3f},{y:.3f}" for x, y, _ in coordinates)
    circles = "\n".join(
        f'  <circle cx="{x:.3f}" cy="{y:.3f}" r="5" fill="#2563eb"><title>n={count}</title></circle>'
        for x, y, count in coordinates)
    ticks = []
    for index in range(6):
        value = index / 5
        x = left + value * plot
        y = top + (1 - value) * plot
        ticks += [f'  <text x="{x:.3f}" y="{top+plot+24}" text-anchor="middle">{value:.1f}</text>',
                  f'  <text x="{left-14}" y="{y+5:.3f}" text-anchor="end">{value:.1f}</text>']
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="100%" height="100%" fill="white"/>
  <text x="{width/2}" y="24" text-anchor="middle" font-size="18">NFL Player-Prop Reliability</text>
  <line x1="{left}" y1="{top+plot}" x2="{left+plot}" y2="{top}" stroke="#9ca3af" stroke-dasharray="6 4"/>
  <line x1="{left}" y1="{top}" x2="{left}" y2="{top+plot}" stroke="black"/>
  <line x1="{left}" y1="{top+plot}" x2="{left+plot}" y2="{top+plot}" stroke="black"/>
  <polyline points="{polyline}" fill="none" stroke="#2563eb" stroke-width="2"/>
{circles}
{chr(10).join(ticks)}
  <text x="{left+plot/2}" y="{height-22}" text-anchor="middle">Mean predicted probability</text>
  <text x="22" y="{top+plot/2}" text-anchor="middle" transform="rotate(-90 22 {top+plot/2})">Observed win rate</text>
</svg>
'''
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8", newline="\n")


def build_experiment_result(*, model_id: str, season: int, requested_weeks: tuple[int, int],
                            rows: list[dict[str, Any]], seed: int, simulations: int,
                            configuration: dict[str, Any], input_hashes: dict[str, str],
                            artifact_paths: Iterable[Path], training_strategy: str,
                            training_weeks: list[int], leakage_safe: bool,
                            out_of_sample: bool) -> dict[str, Any]:
    """Create the common contract consumed by the immutable model registry."""
    evaluated_weeks = sorted({int(row["week"]) for row in rows})
    config_hash = content_hash(configuration)
    current_commit = git_commit()
    identity_hash = content_hash({"model_id": model_id, "git_commit": current_commit,
                                  "configuration_hash": config_hash,
                                  "input_hashes": dict(sorted(input_hashes.items()))})
    experiment_id = (f"{model_id}.{season}.w{requested_weeks[0]:02d}-w{requested_weeks[1]:02d}."
                     f"{identity_hash[:12]}")
    calibration = probability_metrics(rows, "model_probability")
    overall = _metric(rows)
    overall["roi_uncertainty"] = _roi_uncertainty(rows, seed)
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "model_id": model_id,
        "git_commit": current_commit,
        "configuration_hash": config_hash,
        "configuration": configuration,
        "training_window": {"strategy": training_strategy, "weeks": training_weeks},
        "evaluation_window": {"season": season,
                              "requested_weeks": list(range(requested_weeks[0], requested_weeks[1] + 1)),
                              "evaluated_weeks": evaluated_weeks},
        "dataset": {"opportunities": len(rows),
                    "independent_games": len({str(row.get("game_id")) for row in rows}),
                    "input_hashes": dict(sorted(input_hashes.items()))},
        "metrics": {"overall": overall,
                    "by_market": _segments(rows, "market"),
                    "by_confidence_bucket": _segments(rows, "probability_bucket"),
                    "calibration_curve": calibration["bins"]},
        "reproducibility": {"seed": seed, "simulations": simulations,
                            "network_contacted": False, "deterministic": True},
        "evidence": {"leakage_safe": leakage_safe, "out_of_sample": out_of_sample,
                     "status": "PROMOTION_ELIGIBLE_SAMPLE" if len(evaluated_weeks) >= 15 else "INSUFFICIENT_HISTORY"},
        "baseline_comparison": None,
        "artifacts": _artifact_records(artifact_paths),
    }
