from __future__ import annotations

import json

import pytest

from backtesting.compare_nfl_distribution_research import compare_runs


def test_paired_comparison_uses_identical_keys_and_preserves_negative_result(tmp_path):
    base = [{"season": 2025, "week": 1, "game_id": "g", "canonical_player_id": "p",
             "market": "receptions", "actual": 5, "expected_output": 4, "crps": 1}]
    candidate = [{**base[0], "expected_output": 3, "crps": 2}]
    left = tmp_path / "left.json"; right = tmp_path / "right.json"
    left.write_text(json.dumps(base)); right.write_text(json.dumps(candidate))
    report = compare_runs(left, right)
    assert report["matched_rows"] == 1
    assert report["assessment"]["promotion_supported"] is False
    assert report["markets"][0]["delta_candidate_minus_baseline"]["mae"] == pytest.approx(1)


def test_paired_comparison_rejects_inconsistent_outcomes(tmp_path):
    common = {"season": 2025, "week": 1, "game_id": "g", "canonical_player_id": "p",
              "market": "receptions", "expected_output": 4, "crps": 1}
    left = tmp_path / "left.json"; right = tmp_path / "right.json"
    left.write_text(json.dumps([{**common, "actual": 5}]))
    right.write_text(json.dumps([{**common, "actual": 6}]))
    with pytest.raises(ValueError, match="paired outcomes differ"):
        compare_runs(left, right)
