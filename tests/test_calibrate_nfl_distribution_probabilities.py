from __future__ import annotations

import json
from pathlib import Path

import pytest

from backtesting.calibrate_nfl_distribution_probabilities import (
    build_report,
    nested_calibrate,
    run_calibration,
)


def _rows(*, weeks: int = 8, rows_per_week: int = 60) -> list[dict]:
    rows = []
    for week in range(1, weeks + 1):
        for index in range(rows_per_week):
            probability = .55 + .4 * ((index % 10) / 9)
            # Deliberately overconfident but deterministic outcomes.
            won = (index * 7 + week * 3) % 100 < int((.5 + .25 * (probability - .5)) * 100)
            rows.append({
                "season": 2025, "week": week, "game_id": f"g-{week}-{index}",
                "canonical_player_id": f"p-{index}", "market": "receiving_yards",
                "line": 50.5, "side": "OVER", "raw_probability": probability,
                "result": "WIN" if won else "LOSS", "stability_class": "HIGH_STABILITY",
                "stability_score": 75.0, "raw_decision": "PASS",
            })
    return rows


def test_current_week_outcomes_never_affect_current_calibration() -> None:
    original = _rows()
    changed = [{**row, "result": ("LOSS" if row["result"] == "WIN" else "WIN")}
               if row["week"] == 8 else row for row in original]
    first, _folds = nested_calibrate(original, min_fit_rows=60, min_validation_rows=30,
                                     validation_periods=2)
    second, _folds = nested_calibrate(changed, min_fit_rows=60, min_validation_rows=30,
                                      validation_periods=2)
    first_week = [row for row in first if row["week"] == 8]
    second_week = [row for row in second if row["week"] == 8]
    assert [row["calibrated_probability"] for row in first_week] == pytest.approx(
        [row["calibrated_probability"] for row in second_week]
    )
    assert [row["calibration_method"] for row in first_week] == [row["calibration_method"] for row in second_week]


def test_calibration_training_window_is_strictly_prior() -> None:
    calibrated, folds = nested_calibrate(_rows(), min_fit_rows=60, min_validation_rows=30,
                                         validation_periods=2)
    for row in calibrated:
        last = row["calibration_training_last_period"]
        assert last is None or tuple(last) < (row["season"], row["week"])
    assert all(fold["selection"]["status"] in {
        "INSUFFICIENT_PRIOR_INNER_VALIDATION", "PRIOR_INNER_BRIER_SELECTED"
    } for fold in folds)


def test_report_requires_trading_relevant_calibration_gates() -> None:
    calibrated, folds = nested_calibrate(_rows(), min_fit_rows=60, min_validation_rows=30,
                                         validation_periods=2)
    report = build_report(calibrated, folds)
    assert report["research_only"] is True
    assert report["promotion"]["decision"] in {"RETAIN_RESEARCH_ONLY", "ADVANCE_TO_PRICE_AWARE_POLICY_EVALUATION"}
    assert report["promotion"]["production_promotion_eligible"] is False
    assert report["overall"]["raw"]["rows"] == report["overall"]["calibrated"]["rows"]


def test_repeated_runs_are_byte_deterministic(tmp_path: Path) -> None:
    input_path = tmp_path / "thresholds.csv"
    fields = [
        "season", "week", "game_id", "canonical_player_id", "player_name", "team", "opponent",
        "market", "line", "side", "probability", "result", "stability_class", "stability_score", "decision",
    ]
    lines = [",".join(fields)]
    for row in _rows(weeks=5, rows_per_week=20):
        values = [row.get("raw_probability") if field == "probability" else row.get("raw_decision")
                  if field == "decision" else row.get(field, "") for field in fields]
        lines.append(",".join(str(value) for value in values))
    input_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    first, second = tmp_path / "first", tmp_path / "second"
    run_calibration(input_path=input_path, output_dir=first, min_fit_rows=20,
                    min_validation_rows=10, validation_periods=1)
    run_calibration(input_path=input_path, output_dir=second, min_fit_rows=20,
                    min_validation_rows=10, validation_periods=1)
    for name in ("calibration_summary.json", "calibration_rows.json", "calibration_folds.json",
                 "calibration_buckets.json", "manifest.json"):
        # Manifest input paths differ by neither content nor location in this test.
        assert (first / name).read_bytes() == (second / name).read_bytes()
    assert json.loads((first / "manifest.json").read_text())["network_contacted"] is False
