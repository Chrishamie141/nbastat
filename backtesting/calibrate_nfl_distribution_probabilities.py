"""Leakage-safe calibration research for frozen NFL distribution probabilities.

The input rows must be untouched outer-fold forecasts.  For every test week,
calibrator selection and fitting use only forecasts whose week precedes the
test week.  This module changes neither distribution centers nor quantiles.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from sklearn.isotonic import IsotonicRegression


MODEL_ID = "nfl_distribution_probability_calibration_research_v1"
METHODS = ("identity", "shrink_25", "shrink_50", "shrink_75", "isotonic")


def _period(row: dict[str, Any]) -> tuple[int, int]:
    return int(row["season"]), int(row["week"])


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_threshold_rows(path: Path) -> list[dict[str, Any]]:
    """Expand each frozen line into OVER and UNDER probability forecasts."""
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for raw in csv.DictReader(handle):
            if raw.get("result") not in {"WIN", "LOSS", "PUSH"}:
                continue
            actual, line = float(raw["actual"]), float(raw["line"])
            for side in ("OVER", "UNDER"):
                result = "PUSH" if actual == line else "WIN" if (
                    (side == "OVER" and actual > line) or (side == "UNDER" and actual < line)
                ) else "LOSS"
                rows.append({
                    "season": int(raw["season"]), "week": int(raw["week"]),
                    "game_id": raw["game_id"], "canonical_player_id": raw["canonical_player_id"],
                    "player_name": raw.get("player_name"), "team": raw.get("team"),
                    "opponent": raw.get("opponent"), "market": raw["market"],
                    "line": line, "side": side, "actual": actual,
                    "raw_probability": float(raw[f"{side.lower()}_probability"]), "result": result,
                    "stability_class": raw.get("stability_class"),
                    "stability_score": float(raw["stability_score"]),
                    "raw_decision": raw.get("decision"),
                })
    return sorted(rows, key=lambda row: (_period(row), row["game_id"], row["canonical_player_id"],
                                         row["market"], row["line"], row["side"]))


def _outcome(row: dict[str, Any]) -> float:
    return 1.0 if row["result"] == "WIN" else 0.0


def _shrink(probabilities: np.ndarray, factor: float) -> np.ndarray:
    return np.clip(.5 + factor * (probabilities - .5), 1e-6, 1 - 1e-6)


def _fit_predict(method: str, train: Sequence[dict[str, Any]], probabilities: Sequence[float]) -> np.ndarray:
    values = np.asarray(probabilities, dtype=float)
    if method == "identity":
        return np.clip(values, 1e-6, 1 - 1e-6)
    if method.startswith("shrink_"):
        return _shrink(values, int(method.split("_")[1]) / 100.0)
    if method != "isotonic":
        raise ValueError(f"unknown calibration method: {method}")
    x = np.asarray([row["raw_probability"] for row in train], dtype=float)
    y = np.asarray([_outcome(row) for row in train], dtype=float)
    model = IsotonicRegression(y_min=1e-6, y_max=1 - 1e-6, out_of_bounds="clip")
    model.fit(x, y)
    return np.asarray(model.predict(values), dtype=float)


def _brier(rows: Sequence[dict[str, Any]], probabilities: Sequence[float]) -> float:
    outcomes = np.asarray([_outcome(row) for row in rows], dtype=float)
    predicted = np.asarray(probabilities, dtype=float)
    return float(np.mean((predicted - outcomes) ** 2))


def _select_method(prior: Sequence[dict[str, Any]], *, min_fit_rows: int,
                   min_validation_rows: int, validation_periods: int) -> tuple[str, dict[str, Any]]:
    periods = sorted({_period(row) for row in prior})
    validation_set = set(periods[-validation_periods:])
    fit = [row for row in prior if _period(row) not in validation_set]
    validation = [row for row in prior if _period(row) in validation_set]
    if len(fit) < min_fit_rows or len(validation) < min_validation_rows:
        return "identity", {
            "status": "INSUFFICIENT_PRIOR_INNER_VALIDATION", "prior_rows": len(prior),
            "fit_rows": len(fit), "validation_rows": len(validation), "scores": {},
        }
    scores: dict[str, float] = {}
    inputs = [row["raw_probability"] for row in validation]
    for method in METHODS:
        scores[method] = _brier(validation, _fit_predict(method, fit, inputs))
    selected = min(scores, key=lambda method: (scores[method], method))
    return selected, {
        "status": "PRIOR_INNER_BRIER_SELECTED", "prior_rows": len(prior),
        "fit_rows": len(fit), "validation_rows": len(validation),
        "validation_periods": [list(period) for period in sorted(validation_set)],
        "scores": scores,
    }


def nested_calibrate(rows: Sequence[dict[str, Any]], *, min_fit_rows: int = 250,
                     min_validation_rows: int = 100, validation_periods: int = 4) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Calibrate each market/side/week using strictly earlier forecast outcomes."""
    gradeable = [row for row in rows if row["result"] in {"WIN", "LOSS"}]
    periods = sorted({_period(row) for row in gradeable})
    output: list[dict[str, Any]] = []
    folds: list[dict[str, Any]] = []
    groups = sorted({(row["market"], row["side"]) for row in gradeable})
    for test_period in periods:
        for market, side in groups:
            prior = [row for row in gradeable if row["market"] == market and row["side"] == side
                     and _period(row) < test_period]
            test = [row for row in gradeable if row["market"] == market and row["side"] == side
                    and _period(row) == test_period]
            if not test:
                continue
            method, selection = _select_method(
                prior, min_fit_rows=min_fit_rows, min_validation_rows=min_validation_rows,
                validation_periods=validation_periods,
            )
            calibrated = _fit_predict(method, prior, [row["raw_probability"] for row in test])
            calibration_ready = selection["status"] == "PRIOR_INNER_BRIER_SELECTED"
            for row, probability in zip(test, calibrated):
                output.append({
                    **row, "calibrated_probability": float(probability),
                    "calibration_method": method,
                    "calibration_ready": calibration_ready,
                    "calibration_training_last_period": list(max((_period(item) for item in prior), default=(0, 0)))
                    if prior else None,
                    "calibration_training_rows": len(prior), "research_only": True,
                })
            folds.append({
                "test_season": test_period[0], "test_week": test_period[1], "market": market,
                "side": side, "test_rows": len(test), "prior_rows": len(prior),
                "method": method, "selection": selection,
            })
    return sorted(output, key=lambda row: (_period(row), row["game_id"], row["canonical_player_id"],
                                            row["market"], row["line"], row["side"])), folds


def _metrics(rows: Sequence[dict[str, Any]], field: str) -> dict[str, Any]:
    if not rows:
        return {"rows": 0, "brier": None, "log_loss": None, "average_probability": None,
                "hit_rate": None, "calibration_error": None}
    probabilities = np.clip(np.asarray([row[field] for row in rows], dtype=float), 1e-6, 1 - 1e-6)
    outcomes = np.asarray([_outcome(row) for row in rows], dtype=float)
    return {
        "rows": len(rows), "brier": float(np.mean((probabilities - outcomes) ** 2)),
        "log_loss": float(np.mean(-(outcomes * np.log(probabilities) + (1 - outcomes) * np.log(1 - probabilities)))),
        "average_probability": float(np.mean(probabilities)), "hit_rate": float(np.mean(outcomes)),
        "calibration_error": float(np.mean(outcomes) - np.mean(probabilities)),
    }


def _calibration_buckets(rows: Sequence[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    buckets = []
    for index in range(10):
        lower, upper = index / 10, (index + 1) / 10
        selected = [row for row in rows if lower <= float(row[field]) < upper or
                    (index == 9 and float(row[field]) == 1)]
        buckets.append({"lower": lower, "upper": upper, **_metrics(selected, field)})
    return buckets


def build_report(rows: Sequence[dict[str, Any]], folds: Sequence[dict[str, Any]]) -> dict[str, Any]:
    raw, calibrated = _metrics(rows, "raw_probability"), _metrics(rows, "calibrated_probability")
    ready = [row for row in rows if row["calibration_ready"]]
    by_market = []
    for market in sorted({row["market"] for row in rows}):
        selected = [row for row in rows if row["market"] == market]
        by_market.append({"market": market, "raw": _metrics(selected, "raw_probability"),
                          "calibrated": _metrics(selected, "calibrated_probability")})
    # Forecasts emitted before enough prior periods exist remain in the honest
    # full-window score, but cannot qualify as calibrated trading signals.
    high_confidence = [row for row in ready if float(row["calibrated_probability"]) >= .70]
    high_metrics = _metrics(high_confidence, "calibrated_probability")
    calibration_gate = (
        calibrated["brier"] < raw["brier"] and calibrated["log_loss"] < raw["log_loss"]
        and high_metrics["rows"] >= 500 and abs(float(high_metrics["calibration_error"])) <= .05
        and len({_period(row) for row in high_confidence}) >= 15
    )
    return {
        "schema_version": 1, "model_id": MODEL_ID, "research_only": True,
        "method_selection": "INNER_PRIOR_PERIOD_BRIER_ONLY",
        "fitting_policy": "STRICTLY_PRIOR_OUTER_FOLD_FORECAST_OUTCOMES_ONLY",
        "overall": {"raw": raw, "calibrated": calibrated,
                    "delta_calibrated_minus_raw": {"brier": calibrated["brier"] - raw["brier"],
                                                     "log_loss": calibrated["log_loss"] - raw["log_loss"]}},
        "calibration_ready": {"rows": len(ready), "periods": len({_period(row) for row in ready}),
                              "metrics": _metrics(ready, "calibrated_probability")},
        "by_market": by_market, "high_confidence_calibrated": high_metrics,
        "high_confidence_calibrated_periods": len({_period(row) for row in high_confidence}),
        "method_counts": dict(sorted(Counter(row["calibration_method"] for row in rows).items())),
        "folds": len(folds),
        "promotion": {"probability_calibration_gate_passed": calibration_gate,
                      "production_promotion_eligible": False,
                      "decision": "ADVANCE_TO_PRICE_AWARE_POLICY_EVALUATION" if calibration_gate else "RETAIN_RESEARCH_ONLY",
                      "requires_brier_and_log_loss_improvement": True,
                      "requires_high_confidence_calibration": True,
                      "production_blocker": "ABSOLUTE_ROI_AND_LOWER_CONFIDENCE_BOUND_NOT_YET_EVALUATED"},
    }


def run_calibration(*, input_path: Path, output_dir: Path, min_fit_rows: int = 250,
                    min_validation_rows: int = 100, validation_periods: int = 4) -> dict[str, Any]:
    source = load_threshold_rows(input_path)
    rows, folds = nested_calibrate(source, min_fit_rows=min_fit_rows,
                                   min_validation_rows=min_validation_rows,
                                   validation_periods=validation_periods)
    report = build_report(rows, folds)
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, Any] = {
        "calibration_summary.json": report, "calibration_rows.json": rows,
        "calibration_folds.json": folds,
        "calibration_buckets.json": {
            "raw": _calibration_buckets(rows, "raw_probability"),
            "calibrated": _calibration_buckets(rows, "calibrated_probability"),
        },
    }
    for name, value in artifacts.items():
        _write_json(output_dir / name, value)
    paths = [output_dir / name for name in artifacts]
    manifest = {
        "schema_version": 1, "model_id": MODEL_ID,
        "input": {input_path.as_posix(): _hash(input_path)},
        "artifacts": {path.name: _hash(path) for path in sorted(paths, key=lambda item: item.name)},
        "configuration": {"min_fit_rows": min_fit_rows, "min_validation_rows": min_validation_rows,
                          "validation_periods": validation_periods},
        "leakage_control": "CALIBRATOR_SELECTION_AND_FIT_PRECEDE_EACH_TEST_PERIOD",
        "network_contacted": False,
    }
    _write_json(output_dir / "manifest.json", manifest)
    return {**artifacts, "manifest.json": manifest}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", dest="input_path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--min-fit-rows", type=int, default=250)
    parser.add_argument("--min-validation-rows", type=int, default=100)
    parser.add_argument("--validation-periods", type=int, default=4)
    args = parser.parse_args(argv)
    run_calibration(**vars(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
