from pathlib import Path

from backtesting.config import BacktestConfig
from backtesting.grader import PredictionGrader
from backtesting.replay_engine import ReplayEngine


class StubProvider:
    def __init__(self):
        self.calls = []

    def get_games(self, league, season, week):
        self.calls.append(("games", week))
        return [{"id": f"game-{week}"}]

    def get_odds(self, league, season, week):
        self.calls.append(("odds", week))
        return []

    def get_weather(self, league, season, week):
        self.calls.append(("weather", week))
        return []

    def get_injuries(self, league, season, week):
        self.calls.append(("injuries", week))
        return []

    def get_player_stats(self, league, season, week):
        self.calls.append(("player_stats", week))
        return []

    def get_team_stats(self, league, season, week):
        self.calls.append(("team_stats", week))
        return []

    def get_outcomes(self, league, season, week):
        self.calls.append(("outcomes", week))
        return [{"game": f"game-{week}", "market": "moneyline", "actual_result": "home"}]


def test_replay_engine_freezes_then_grades_chronologically(tmp_path):
    provider = StubProvider()

    def factory(provider, config, week):
        provider.get_games(config.league, config.season, week)
        provider.get_odds(config.league, config.season, week)
        return [{"game": f"game-{week}", "market": "moneyline", "prediction": "home", "confidence": 72}]

    config = BacktestConfig(
        league="nfl",
        season="2025",
        start_week=1,
        end_week=2,
        export=False,
        db_path=tmp_path / "backtests.db",
        data_dir=tmp_path,
        results_dir=tmp_path / "results",
    )

    summary = ReplayEngine(config, provider=provider, prediction_factory=factory).run()

    assert summary["metrics"]["overall_accuracy"] == 1.0
    assert provider.calls == [
        ("games", 1),
        ("odds", 1),
        ("games", 1),
        ("odds", 1),
        ("outcomes", 1),
        ("games", 2),
        ("odds", 2),
        ("games", 2),
        ("odds", 2),
        ("outcomes", 2),
    ]


def test_grader_supports_over_under_push_and_moneyline():
    grader = PredictionGrader()
    assert grader.grade({"market": "player_prop", "prediction": "over", "line": 10}, {"actual_result": 12})["correct"] is True
    assert grader.grade({"market": "player_prop", "prediction": "under", "line": 10}, {"actual_result": 10})["grade"] == "push"
    assert grader.grade({"market": "moneyline", "prediction": "away"}, {"actual_result": "home"})["correct"] is False

import json
import shutil
import subprocess
import sys

import pytest

from backtesting.historical_provider import HistoricalSnapshotProvider
from backtesting.snapshots import SnapshotError, validate_snapshot


def test_missing_snapshots_produce_clear_error(tmp_path):
    provider = HistoricalSnapshotProvider(tmp_path)
    with pytest.raises(SnapshotError, match="No games snapshot found for NFL 2025 Week 1") as exc:
        provider.get_games("nfl", "2025", 1)
    assert "week_01/games.json" in str(exc.value)


def test_validation_reports_missing_datasets(tmp_path):
    week_dir = tmp_path / "nfl" / "2025" / "week_01"
    week_dir.mkdir(parents=True)
    (week_dir / "games.json").write_text(json.dumps([{
        "game_id": "g1", "league": "nfl", "season": "2025", "week": 1,
        "kickoff_time": "2025-09-07T17:00:00Z", "home_team": "BUF",
        "away_team": "MIA", "venue": "Fixture", "status": "final"
    }]))
    report = validate_snapshot(tmp_path, "nfl", "2025")
    assert not report.ok
    assert any("Missing outcomes file" in error for error in report.errors)


def test_importing_json_creates_normalized_snapshot_folder(tmp_path):
    source = tmp_path / "raw.json"
    source.write_text(json.dumps({
        "games": [{"id": "g1", "kickoff_time": "2025-09-07T17:00:00Z", "home_team": "BUF", "away_team": "MIA", "venue": "Fixture", "status": "final"}],
        "outcomes": [{"game_id": "g1", "final_home_score": 1, "final_away_score": 0, "player_results": {}, "market_results": {}, "completed_at": "2025-09-07T20:00:00Z"}]
    }))
    result = subprocess.run([sys.executable, "-m", "backtesting.import_historical_data", "--league", "nfl", "--season", "2025", "--week", "1", "--source", str(source), "--format", "json", "--data-dir", str(tmp_path / "snapshots")], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "snapshots" / "nfl" / "2025" / "week_01" / "games.json").exists()


def test_importing_csv_creates_valid_normalized_json(tmp_path):
    source = tmp_path / "raw.csv"
    source.write_text("dataset,id,kickoff_time,home_team,away_team,venue,status,game_id,final_home_score,final_away_score,player_results,market_results,completed_at\n"
                      "games,g1,2025-09-07T17:00:00Z,BUF,MIA,Fixture,final,,,,,,\n"
                      "outcomes,,,,,,,g1,1,0,{}, {},2025-09-07T20:00:00Z\n")
    result = subprocess.run([sys.executable, "-m", "backtesting.import_historical_data", "--league", "nfl", "--season", "2025", "--week", "1", "--source", str(source), "--format", "csv", "--data-dir", str(tmp_path / "snapshots")], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    games = json.loads((tmp_path / "snapshots" / "nfl" / "2025" / "week_01" / "games.json").read_text())
    assert games[0]["game_id"] == "g1"


def test_one_week_fixture_replay_produces_predictions_and_grades(tmp_path):
    src = Path("tests/fixtures/backtesting")
    data_dir = tmp_path / "snapshots"
    shutil.copytree(src, data_dir)
    config = BacktestConfig(league="nfl", season="2025", start_week=1, end_week=1, markets=("PASS_YDS",), db_path=tmp_path / "backtests.db", data_dir=data_dir, results_dir=tmp_path / "results")
    summary = ReplayEngine(config).run()
    assert summary["metrics"]["total_predictions"] > 0
    assert summary["metrics"]["graded_predictions"] > 0
    assert summary["metrics"]["overall_accuracy"] is not None
    predictions_csv = Path(summary["report_dir"]) / "predictions.csv"
    assert predictions_csv.exists()
    assert predictions_csv.read_text().strip()


def test_existing_snapshots_not_overwritten_without_overwrite(tmp_path):
    source = tmp_path / "raw.json"
    source.write_text(json.dumps({"games": [], "outcomes": []}))
    cmd = [sys.executable, "-m", "backtesting.import_historical_data", "--league", "nfl", "--season", "2025", "--week", "1", "--source", str(source), "--format", "json", "--data-dir", str(tmp_path / "snapshots")]
    first = subprocess.run(cmd, capture_output=True, text=True)
    second = subprocess.run(cmd, capture_output=True, text=True)
    assert first.returncode == 0
    assert second.returncode != 0
    assert "Refusing to overwrite" in (second.stdout + second.stderr)
