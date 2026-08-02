from __future__ import annotations

import json
from pathlib import Path

import pytest

from backtesting.research_nfl_player_stat_distributions import (
    MODEL_ID,
    _non_crossing,
    _select_configuration,
    _weekly_projection_metrics,
    add_stability_scores,
    apply_thresholds,
    baseline_comparison,
    build_distribution_samples,
    calibration_tables,
    parlay_dependency_diagnostics,
)


def _write_week(root: Path, season: int, week: int, actual: float) -> None:
    directory = root / "nfl" / str(season) / f"week_{week:02d}"
    directory.mkdir(parents=True, exist_ok=True)
    game_id = f"game-{season}-{week}"
    games = [{
        "season": season, "week": week, "game_id": game_id,
        "home_team": "BUF", "away_team": "MIA",
        "kickoff_time": f"{season}-09-{week:02d}T17:00:00Z",
    }]
    stats = [{
        "season": season, "week": week, "game_id": game_id,
        "record_role": "completed_game_history", "canonical_player_id": "player-1",
        "player_name": "Player One", "team": "BUF", "category": "receiving",
        "receiving_yards": actual, "stats": {"receiving_yards": actual, "targets": 5},
    }]
    (directory / "games.json").write_text(json.dumps(games), encoding="utf-8")
    (directory / "player_stats.json").write_text(json.dumps(stats), encoding="utf-8")


def _projection() -> dict:
    return {
        "season": 2025, "week": 3, "game_id": "g1", "canonical_player_id": "p1",
        "player_name": "P One", "team": "BUF", "opponent": "MIA",
        "home_away": "HOME", "market": "receiving_yards", "actual": 70.0,
        "archetype": "HIGH_VOLUME_RECEIVER", "opponent_strength": "AVERAGE",
        "weather_bucket": "UNAVAILABLE", "history_depth": 12,
        "expected_output": 65.0, "median_output": 64.0,
        "p10": 45.0, "p25": 55.0, "p50": 64.0, "p75": 73.0, "p90": 82.0,
        "interval_width_50": 18.0, "interval_width_80": 37.0,
        "predicted_variance": 225.0, "zero_rate": 0.0,
        "configuration": "parametric_rich_student_t", "distribution_family": "student_t",
        "feature_set": "rich", "model_disagreement": 1.0,
        "historical_output_std": 14.0, "recent_output_std": 12.0,
        "usage_mean": .20, "usage_volatility": .02, "recent_participation_rate": 1.0,
        "rolling_mean_5": 62.0, "ewm_recent": 63.0, "season_mean": 61.0,
        "rolling_median": 60.0, "opponent_strength_numeric": 1.05,
        "baseline_p10": 40.0, "baseline_p25": 50.0, "baseline_p50": 60.0,
        "baseline_p75": 72.0, "baseline_p90": 85.0,
        "crps": 8.0, "pinball": {"p10": 2.5, "p25": 3.0, "p50": 3.0, "p75": 2.0, "p90": 1.2},
        "research_only": True,
    }


def test_feature_builder_does_not_use_current_week_outcome(tmp_path: Path) -> None:
    _write_week(tmp_path, 2023, 1, 10.0)
    _write_week(tmp_path, 2023, 2, 20.0)
    _write_week(tmp_path, 2023, 3, 1000.0)
    samples, _inputs, identity_audit = build_distribution_samples(tmp_path, (2023,), min_history=2)
    week_three = next(row for row in samples if row["week"] == 3)
    assert week_three["features"]["career_mean"] == pytest.approx(15.0)
    assert week_three["features"]["rolling_mean_5"] == pytest.approx(15.0)
    assert week_three["actual"] == 1000.0
    assert identity_audit["completed_stat_rows"] == 3


def test_configuration_selection_uses_only_supplied_prior_oof_rows() -> None:
    prior = [{
        "market": "receiving_yards",
        "candidate_crps": {
            "quantile_direct_rich": 1.0,
            "parametric_rich_student_t": 2.0,
        },
    } for _ in range(4)]
    selected, evidence = _select_configuration("receiving_yards", prior, min_selection_rows=2)
    assert selected == "quantile_direct_rich"
    assert evidence["status"] == "PRIOR_OOF_CRPS_SELECTED"


def test_missing_stat_player_id_uses_existing_unique_identity_artifact(tmp_path: Path) -> None:
    _write_week(tmp_path, 2025, 1, 10.0)
    _write_week(tmp_path, 2025, 2, 20.0)
    directory = tmp_path / "nfl" / "2025" / "week_02"
    stats = json.loads((directory / "player_stats.json").read_text())
    stats[0].pop("canonical_player_id")
    (directory / "player_stats.json").write_text(json.dumps(stats), encoding="utf-8")
    identities = [{
        "game_id": "game-2025-2", "team": "BUF", "player_name": "Player One",
        "normalized_player_name": "player one", "canonical_player_id": "player-1",
    }]
    (directory / "player_identities.json").write_text(json.dumps(identities), encoding="utf-8")
    samples, _inputs, audit = build_distribution_samples(tmp_path, (2025,), min_history=1)
    week_two = next(row for row in samples if row["week"] == 2)
    assert week_two["canonical_player_id"] == "player-1"
    assert audit["identity_resolved_stat_rows"] == 1


def test_quantiles_are_deterministically_non_crossing() -> None:
    assert _non_crossing([10, 8, -2, 12, 9]) == [0.0, 8.0, 9.0, 10.0, 12.0]


def test_stability_is_independent_of_sportsbook_line_and_price() -> None:
    first = add_stability_scores([_projection()])[0]
    with_market_fields = {**_projection(), "line": 55.5, "decimal_odds": 1.91, "market_probability": .52}
    second = add_stability_scores([with_market_fields])[0]
    assert first["stability_score"] == second["stability_score"]
    assert first["stability_class"] == second["stability_class"]


def test_threshold_application_selects_at_most_one_side() -> None:
    projection = add_stability_scores([_projection()])[0]
    thresholds = [{
        "season": 2025, "week": 3, "game_id": "g1", "canonical_player_id": "p1",
        "market": "receiving_yards", "line": 50.5, "books": ["book"],
    }]
    rows = apply_thresholds([projection], thresholds, probability_threshold=.50)
    assert len(rows) == 1
    assert rows[0]["side"] in {"OVER", "UNDER"}
    assert rows[0]["decision"] in {"QUALIFY", "PASS"}
    assert rows[0]["research_only"] is True


def test_parlay_diagnostics_never_assume_independence_without_evidence() -> None:
    projection = add_stability_scores([_projection()])[0]
    report = parlay_dependency_diagnostics([projection])
    assert all(row["independence_allowed"] is False for row in report["relationships"])
    assert "DO_NOT_MULTIPLY" in report["independence_policy"]
    assert MODEL_ID.startswith("nfl_player_stat_distribution")


def test_weekly_macro_metrics_keep_seasons_separate() -> None:
    first = _projection()
    second = {**_projection(), "season": 2024}
    rows = _weekly_projection_metrics([first, second])
    assert [(row["season"], row["week"]) for row in rows] == [(2024, 3), (2025, 3)]


def test_frozen_baseline_comparison_is_paired_on_identical_rows() -> None:
    projection = _projection()
    key = (2025, 3, "g1", "p1", "receiving_yards")
    rows = baseline_comparison([projection], {key: 50.0}, {key: 60.0})
    current_v4 = next(row for row in rows if row["market"] == "ALL" and row["baseline"] == "current_v4")
    assert current_v4["rows"] == 1
    assert current_v4["candidate_mae_on_same_rows"] == pytest.approx(5.0)
    assert current_v4["paired_mae_delta"] == pytest.approx(-5.0)


def test_calibration_reports_over_and_under_separately() -> None:
    projection = add_stability_scores([_projection()])[0]
    threshold = {
        "season": 2025, "week": 3, "game_id": "g1", "canonical_player_id": "p1",
        "market": "receiving_yards", "line": 50.5, "books": ["book"],
    }
    threshold_rows = apply_thresholds([projection], [threshold], probability_threshold=.50)
    by_market, _by_stability, _critical = calibration_tables(threshold_rows)
    receiving = next(row for row in by_market if row["market"] == "receiving_yards")
    assert receiving["over"]["forecasts"] == 1
    assert receiving["under"]["forecasts"] == 1
    assert receiving["over"]["empirical_hit_rate"] == 1.0
    assert receiving["under"]["empirical_hit_rate"] == 0.0
