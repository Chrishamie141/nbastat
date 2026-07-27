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
