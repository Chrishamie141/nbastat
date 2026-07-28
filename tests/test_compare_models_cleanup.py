import shutil
import tempfile
from pathlib import Path

import pytest

from backtesting import compare_models
from backtesting.compare_models import compare, write_artifacts
from backtesting.config import BacktestConfig, SNAPSHOTS_DIR
from backtesting.prediction_store import PredictionStore
from backtesting.replay_engine import ReplayEngine
from backtesting.snapshots import SnapshotError


def _comparison(data_dir: Path):
    return compare(
        data_dir=data_dir,
        league="nfl",
        season="2025",
        start_week=1,
        end_week=1,
        markets=("h2h",),
        models=("nfl_game_baseline_v1", "nfl_game_baseline_v2"),
    )


def test_comparison_closes_temp_databases_and_writes_artifacts(tmp_path, monkeypatch):
    data_dir = tmp_path / "explicit-snapshots"
    shutil.copytree(Path("tests/fixtures/backtesting"), data_dir)
    temporary_paths = []
    real_temporary_directory = tempfile.TemporaryDirectory

    class TrackingTemporaryDirectory(real_temporary_directory):
        def __enter__(self):
            path = super().__enter__()
            temporary_paths.append(Path(path))
            return path

    monkeypatch.setattr(compare_models.tempfile, "TemporaryDirectory", TrackingTemporaryDirectory)
    report, rows = _comparison(data_dir)

    assert report["models"].keys() == {"nfl_game_baseline_v1", "nfl_game_baseline_v2"}
    assert temporary_paths and not temporary_paths[0].exists()
    output = tmp_path / "nested" / "comparison.json"
    bets = tmp_path / "other-nested" / "bets.csv"
    write_artifacts(report, rows, output, bets)
    assert output.exists()
    assert bets.exists()


def test_missing_snapshot_preserves_primary_error_and_cleans_temp_dir(tmp_path, monkeypatch):
    temporary_paths = []
    real_temporary_directory = tempfile.TemporaryDirectory

    class TrackingTemporaryDirectory(real_temporary_directory):
        def __enter__(self):
            path = super().__enter__()
            temporary_paths.append(Path(path))
            return path

    monkeypatch.setattr(compare_models.tempfile, "TemporaryDirectory", TrackingTemporaryDirectory)
    with pytest.raises(SnapshotError):
        _comparison(tmp_path / "missing")
    assert temporary_paths and not temporary_paths[0].exists()


def test_shared_default_snapshot_root():
    assert SNAPSHOTS_DIR == Path(compare_models.__file__).resolve().parent / "data" / "snapshots"
    assert BacktestConfig(league="nfl", season="2025").data_dir == SNAPSHOTS_DIR


def test_replay_engine_preserves_injected_store_ownership(tmp_path):
    store = PredictionStore(tmp_path / "injected.db")
    config = BacktestConfig(league="nfl", season="2025", db_path=tmp_path / "unused.db")
    with ReplayEngine(config, store=store):
        pass
    assert store.connect() is not None
    store.close()


def test_diagnostic_helpers_and_markdown_artifact(tmp_path):
    from backtesting.compare_models import calibration_metrics, favorite_underdog, market_metrics, projection_metrics, render_markdown

    rows = [
        {"game_id": "g", "market": "h2h", "grade": "win", "model_probability": .65,
         "consensus_probability": .55, "sportsbook_odds": -110, "line": None, "features": {"projected_home_points": 24,
         "projected_away_points": 20, "projected_total": 44, "projected_margin": 4},
         "final_home_score": 27, "final_away_score": 17},
        # A second market for the same game must not double-weight projection error.
        {"game_id": "g", "market": "spread", "grade": "loss", "model_probability": .55,
         "line": 3.5, "sportsbook_odds": -110, "features": {"projected_home_points": 24, "projected_away_points": 20,
         "projected_total": 44, "projected_margin": 4}, "final_home_score": 27, "final_away_score": 17},
    ]
    calibration = calibration_metrics(rows)
    assert calibration["covered_count"] == 2
    assert calibration["ece"] == pytest.approx((abs(0 - .55) + abs(1 - .65)) / 2)
    assert favorite_underdog(rows[0]) == "favorite"
    assert favorite_underdog(rows[1]) == "underdog"
    projection = projection_metrics(rows)
    assert projection["home_score"] == {"count": 1, "mae": 3, "rmse": 3}
    assert projection["margin"]["mae"] == 6

    report = {"warning": "exploratory", "conclusion": "mixed/inconclusive",
              "dataset": {"games_eligible": 1, "games_discovered": 1, "games_excluded": 0,
                          "readiness": {"1": {"status": "pass", "reasons": []}}},
              "models": {"v1": {"overall": market_metrics(rows)}, "v2": {"overall": market_metrics(rows)}}}
    markdown = render_markdown(report)
    for heading in ("Dataset/readiness", "Weekly performance", "Calibration", "Model agreement",
                    "Projection error", "Recommended V3 research priorities"):
        assert f"## {heading}" in markdown


def test_artifact_csv_has_stable_audit_contract(tmp_path):
    import csv
    from backtesting.compare_models import CSV_FIELDS

    report = {"warning": "exploratory", "conclusion": "mixed/inconclusive", "dataset": {"games_eligible": 0, "games_discovered": 0, "games_excluded": 0, "readiness": {}}, "models": {}}
    rows = []
    output, bets, markdown = tmp_path / "report.json", tmp_path / "bets.csv", tmp_path / "report.md"
    write_artifacts(report, rows, output, bets, markdown)
    assert tuple(next(csv.reader(bets.open()))) == CSV_FIELDS
    assert output.exists() and markdown.exists()
