import json

from backtesting.analyze_nfl_player_prop_roi import analyze, write_outputs


def _rows():
    rows = []
    for game, week, result in (("g1", 1, "WIN"), ("g2", 2, "LOSS"), ("g3", 3, "WIN")):
        rows.append({"game_id": game, "week": week, "market": "receptions", "bookmaker": "book",
                     "side": "OVER", "grade": result, "profit_units": 1.0 if result == "WIN" else -1.0,
                     "candidate_edge": 0.08, "baseline_edge": 0.02,
                     "model_probability": 0.6, "baseline_probability": 0.52})
    return rows


def test_roi_diagnostics_are_deterministic_and_descriptive_only(tmp_path):
    first = analyze(_rows(), thresholds=(0.0, 0.05, 0.10), draws=50, seed=7)
    second = analyze(_rows(), thresholds=(0.0, 0.05, 0.10), draws=50, seed=7)
    assert first == second
    assert first["selection_allowed"] is False
    candidate = [row for row in first["threshold_metrics"]
                 if row["policy"] == "v4_candidate" and row["edge_threshold"] == 0.05][0]
    assert candidate["bets"] == 3
    assert candidate["roi"] == 1 / 3
    baseline = [row for row in first["threshold_metrics"]
                if row["policy"] == "v3_baseline" and row["edge_threshold"] == 0.05][0]
    assert baseline["bets"] == 0

    input_path = tmp_path / "predictions.json"
    input_path.write_text(json.dumps(_rows()))
    write_outputs(first, tmp_path / "out", input_path)
    assert {path.name for path in (tmp_path / "out").iterdir()} == {
        "roi_diagnostics.json", "roi_by_edge_threshold.csv", "roi_segments.csv",
        "roi_diagnostics_manifest.json",
    }
