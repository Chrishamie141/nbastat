import json

import pytest

from backtesting.research_nfl_player_prop_v4 import (
    DISTRIBUTIONS, FEATURES, _cross_fit_variance, _kelly, _probabilities, _walk_forward_calibration_v4,
    _stable_model_definition, build_training_samples,
)


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_weekly_feature_builder_never_reads_current_or_future_target(tmp_path):
    values = [10, 30, 900]
    for week, target in enumerate(values, 1):
        directory = tmp_path / "nfl" / "2024" / f"week_{week:02d}"
        game_id = f"g{week}"
        _write(directory / "games.json", [{"game_id": game_id, "season": 2024, "week": week,
            "home_team": "KC", "away_team": "BUF", "kickoff_time": f"2024-09-{week:02d}T17:00:00Z"}])
        _write(directory / "player_stats.json", [{"game_id": game_id, "season": 2024, "week": week,
            "record_role": "completed_game_history", "player_id": "p1", "player_name": "Player",
            "team": "KC", "category": "receiving",
            "stats": {"receptions": 2, "receiving_yards": target, "targets": 4}}])
    samples, _state, _inputs = build_training_samples(tmp_path, (2024,), min_history=1)
    yards = [row for row in samples if row["market"] == "receiving_yards"]
    assert [row["week"] for row in yards] == [2, 3]
    assert yards[0]["features"]["rolling_mean_all"] == 10
    assert yards[0]["features"]["usage_mean_all"] == 1
    assert yards[1]["features"]["rolling_mean_all"] == 20
    assert yards[1]["target"] == 900
    assert 900 not in yards[1]["features"].values()


def test_every_distribution_backend_is_coherent_and_kelly_is_capped():
    for family in DISTRIBUTIONS:
        probabilities = _probabilities(family, 40.5, 45, 100, .1)
        assert sum(probabilities.values()) == pytest.approx(1)
        assert all(0 <= value <= 1 for value in probabilities.values())
    sizing = _kelly(.7, 0, 2.0, .25, .05)
    assert sizing["expected_value"] == pytest.approx(.4)
    assert sizing["full_kelly"] == pytest.approx(.4)
    assert sizing["fractional_kelly"] == .05


def test_calibration_supports_isotonic_beta_and_platt_walk_forward():
    rows = []
    for week in (1, 2, 3):
        for index in range(30):
            probability = .2 + .6 * (index / 29)
            rows.append({"week": week, "market": "receiving_yards", "model_probability": probability,
                         "grade": "WIN" if (index + week) % 3 else "LOSS"})
    report = _walk_forward_calibration_v4(rows, 1729, min_train_rows=40, min_test_rows=20)
    assert report["status"] == "COMPLETE"
    assert {row["method"] for row in report["folds"]} == {"isotonic", "beta", "platt"}
    assert all(row["test_week"] == 3 for row in report["folds"])


def test_variance_is_cross_fitted_only_from_earlier_residual_folds():
    rows=[]
    for week in (9,11,13):
        for index in range(20):
            rows.append({"market":"receiving_yards","test_week":week,"residual":float(index-week),
                         "features":{name:float(index+1) for name in FEATURES}})
    _cross_fit_variance(rows,1729,min_train_rows=20)
    assert all("predicted_variance" not in row for row in rows if row["test_week"] in {9,11})
    assert all(row["predicted_variance"] >= .25 for row in rows if row["test_week"] == 13)


def test_model_registration_contract_ignores_only_source_commit():
    previous = {"model_id": "m", "state": "experimental", "git_commit": "old"}
    current = {"model_id": "m", "state": "experimental", "git_commit": "new"}
    stable_previous = _stable_model_definition(previous)
    stable_current = _stable_model_definition(current)
    assert stable_previous == stable_current
    assert stable_previous != {"model_id": "m", "state": "candidate"}
