import json

import numpy as np
import pytest

from backtesting.audit_nfl_player_prop_predictions import (
    PredictionAuditIntegrityError, _choose, _metric_variant, audit, prediction_key, write_outputs,
)
from backtesting.build_nfl_player_prop_predictions import distribution_summary


def test_distribution_summary_and_support_are_exact():
    summary = distribution_summary(np.array([0, 1, 1, 2], dtype=float))
    assert summary["count"] == 4
    assert summary["minimum"] == 0
    assert summary["maximum"] == 2
    assert summary["mean"] == summary["median"] == 1
    assert summary["standard_deviation"] == pytest.approx(np.sqrt(.5))
    assert summary["unique_values"] == 3
    assert summary["zero_mass"] == .25
    assert 10 > summary["maximum"]  # an extreme line is visibly outside support


def test_prediction_key_requires_side_line_game_player_and_market():
    row = {"game_id": "g", "canonical_player_id": "p", "market": "receptions",
           "line": 4.5, "side": "OVER"}
    assert prediction_key(row) == ("g", "p", "receptions", 4.5, "OVER")
    assert prediction_key({**row, "side": "UNDER"}) != prediction_key(row)
    assert prediction_key({**row, "line": 5.5}) != prediction_key(row)


def test_swap_detection_and_favored_selection_do_not_read_outcome():
    rows = [
        {"season": 2025, "week": 1, "game_id": "g", "canonical_player_id": "p",
         "market": "receptions", "line": 4.5, "side": "OVER", "grade": "WIN",
         "model_probability": .01, "opposite_probability": .99, "push_probability": 0},
        {"season": 2025, "week": 1, "game_id": "g", "canonical_player_id": "p",
         "market": "receptions", "line": 4.5, "side": "UNDER", "grade": "LOSS",
         "model_probability": .99, "opposite_probability": .01, "push_probability": 0},
    ]
    assert _metric_variant(rows, "swap")["brier_score"] < _metric_variant(rows, "current")["brier_score"]
    assert _choose(rows, "model_probability")[0]["side"] == "UNDER"
    flipped = [{**row, "grade": "LOSS" if row["grade"] == "WIN" else "WIN"} for row in rows]
    assert _choose(flipped, "model_probability")[0]["side"] == "UNDER"


def _snapshot(root):
    directory = root / "nfl" / "2025" / "week_01"; directory.mkdir(parents=True)
    game = {"game_id": "g", "season": 2025, "week": 1,
            "kickoff_time": "2025-09-05T00:00:00Z", "prediction_cutoff": "2025-09-04T23:00:00Z"}
    common = {"season": 2025, "week": 1, "game_id": "g", "canonical_player_id": "p",
              "player_name": "Player", "team": "KC", "market": "receptions", "line": 4.5,
              "bookmaker": "book", "snapshot_timestamp": "2025-09-04T22:00:00Z"}
    quotes = [{**common, "selection": "OVER", "american_odds": -110},
              {**common, "selection": "UNDER", "american_odds": -110}]
    summary = distribution_summary(np.array([3., 4., 5., 6.]))
    predictions = [{**common, "side": side, "model_probability": probability,
                    "over_probability": .5, "under_probability": .5, "push_probability": 0,
                    "readiness": "READY", "simulations": 4, "simulation_seed": 7,
                    "model_version": "test", "distribution_summary": summary,
                    "provenance": {"player_history_games": 3}}
                   for side, probability in (("OVER", .5), ("UNDER", .5))]
    outcome = {"game_id": "g", "canonical_player_id": "p", "record_role": "game_outcome",
               "is_pregame": False, "stats": {"receptions": 5}}
    for name, value in (("games.json", [game]), ("player_prop_odds.json", quotes),
                        ("player_prop_predictions.json", predictions), ("player_stats.json", [outcome])):
        (directory / name).write_text(json.dumps(value))


def test_audit_is_offline_and_artifacts_repeat_identically(tmp_path, monkeypatch):
    _snapshot(tmp_path)
    monkeypatch.setattr("socket.create_connection", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    report = audit(tmp_path, 2025, 1, 1, validate=True)
    assert report["summary"]["duplicate_prediction_keys"] == 0
    assert report["summary"]["both_sides_evaluated"] is True
    assert report["breakdowns"]["over_only"]["count"] == 1
    assert report["breakdowns"]["under_only"]["count"] == 1
    output = tmp_path / "audit"; write_outputs(report, output)
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    write_outputs(audit(tmp_path, 2025, 1, 1, validate=True), output)
    assert {path.name: path.read_bytes() for path in output.iterdir()} == before
    assert set(before) == {"probability_audit_summary.json", "probability_audit_rows.json",
        "extreme_predictions.json", "side_swap_comparison.json", "distribution_diagnostics.json",
        "market_side_breakdowns.json", "audit_validation_findings.json", "audit_manifest.json"}


def test_recoverable_validation_finding_is_artifact_first_and_does_not_raise(tmp_path):
    _snapshot(tmp_path)
    path = tmp_path / "nfl/2025/week_01/player_prop_predictions.json"
    predictions = json.loads(path.read_text())
    predictions[0].pop("distribution_summary")
    path.write_text(json.dumps(predictions))

    output = tmp_path / "audit"
    report = audit(tmp_path, 2025, 1, 1, validate=True, output_dir=output)

    assert report["summary"]["validation"]["fatal_count"] == 0
    assert any(item["code"] == "MISSING_DISTRIBUTION_SUMMARY" for item in report["validation_findings"])
    assert (output / "audit_manifest.json").exists()


def test_fatal_validation_finding_writes_artifacts_then_raises_structured_error(tmp_path):
    _snapshot(tmp_path)
    path = tmp_path / "nfl/2025/week_01/player_prop_predictions.json"
    predictions = json.loads(path.read_text())
    predictions.append({**predictions[0], "model_probability": .75})
    path.write_text(json.dumps(predictions))
    output = tmp_path / "audit"

    with pytest.raises(PredictionAuditIntegrityError) as caught:
        audit(tmp_path, 2025, 1, 1, validate=True, output_dir=output)

    assert caught.value.fatal_count > 0
    assert caught.value.artifact_directory == output
    assert (output / "audit_validation_findings.json").exists()
    assert (output / "audit_manifest.json").exists()
