from argparse import Namespace
from pathlib import Path

from backtesting.config import BacktestConfig, PredictionMode
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
        return [{"game_id": f"game-{week}", "market": "moneyline", "selection": "home", "line": None, "odds": -110, "sportsbook": "fixture-book"}]

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


def test_replay_engine_freezes_then_grades_chronologically(tmp_path, monkeypatch):
    monkeypatch.setattr("backtesting.versioning.git_commit_hash", lambda: "unknown")
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

    engine = ReplayEngine(config, provider=provider, prediction_factory=factory)
    summary = engine.run()

    assert summary["metrics"]["overall_accuracy"] == 1.0
    assert engine.metadata.git_commit_hash == "unknown"
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
import pytest

from backtesting.historical_provider import HistoricalSnapshotProvider
from backtesting.import_historical_data import main as import_historical_main
from backtesting.snapshots import SnapshotError, validate_snapshot


def test_missing_snapshots_produce_clear_error(tmp_path):
    provider = HistoricalSnapshotProvider(tmp_path)
    with pytest.raises(SnapshotError, match="No games snapshot found for NFL 2025 Week 1") as exc:
        provider.get_games("nfl", "2025", 1)
    error_path = Path(str(exc.value).split(": ")[-1])
    assert error_path.parts[-2:] == ("week_01", "games.json")


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
    import_historical_main(Namespace(league="nfl", season="2025", week=1, source=str(source), format="json", data_dir=tmp_path / "snapshots", validate_only=False, overwrite=False))
    assert (tmp_path / "snapshots" / "nfl" / "2025" / "week_01" / "games.json").exists()


def test_importing_csv_creates_valid_normalized_json(tmp_path):
    source = tmp_path / "raw.csv"
    source.write_text("dataset,id,kickoff_time,home_team,away_team,venue,status,game_id,final_home_score,final_away_score,player_results,market_results,completed_at\n"
                      "games,g1,2025-09-07T17:00:00Z,BUF,MIA,Fixture,final,,,,,,\n"
                      "outcomes,,,,,,,g1,1,0,{}, {},2025-09-07T20:00:00Z\n")
    import_historical_main(Namespace(league="nfl", season="2025", week=1, source=str(source), format="csv", data_dir=tmp_path / "snapshots", validate_only=False, overwrite=False))
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
    args = Namespace(league="nfl", season="2025", week=1, source=str(source), format="json", data_dir=tmp_path / "snapshots", validate_only=False, overwrite=False)
    import_historical_main(args)
    with pytest.raises(SnapshotError, match="Refusing to overwrite"):
        import_historical_main(args)


class NoOddsProvider(StubProvider):
    def get_odds(self, league, season, week):
        self.calls.append(("odds", week))
        return []


def test_betting_mode_generates_zero_predictions_without_odds(tmp_path):
    provider = NoOddsProvider()
    config = BacktestConfig(league="nfl", season="2025", start_week=1, end_week=1, export=False, db_path=tmp_path/"b.db", data_dir=tmp_path, prediction_mode=PredictionMode.BETTING)
    engine = ReplayEngine(config, provider=provider, prediction_factory=lambda p, c, w: [{"game":"game-1","market":"moneyline","prediction":"home","confidence":70}])
    summary = engine.run()
    assert summary["mode"] == "BETTING"
    assert summary["metrics"]["total_predictions"] == 0


def test_statistical_mode_allows_predictions_without_odds(tmp_path):
    provider = NoOddsProvider()
    config = BacktestConfig(league="nfl", season="2025", start_week=1, end_week=1, export=False, db_path=tmp_path/"s.db", data_dir=tmp_path, prediction_mode=PredictionMode.STATISTICAL)
    engine = ReplayEngine(config, provider=provider, prediction_factory=lambda p, c, w: [{"game":"game-1","market":"moneyline","prediction":"home","confidence":70}])
    summary = engine.run()
    assert summary["mode"] == "STATISTICAL"
    assert summary["metrics"]["graded_predictions"] == 1


def test_roi_uses_american_odds_profit(tmp_path):
    provider = StubProvider()
    config = BacktestConfig(league="nfl", season="2025", start_week=1, end_week=1, export=False, db_path=tmp_path/"r.db", data_dir=tmp_path)
    pred = {"game":"game-1","market":"moneyline","prediction":"home","confidence":70,"sportsbook_odds":100,"edge":0.05,"clv":0.5}
    summary = ReplayEngine(config, provider=provider, prediction_factory=lambda p, c, w: [pred]).run()
    assert summary["metrics"]["roi"] == 1.0
    assert summary["metrics"]["average_edge"] == 0.05
    assert summary["metrics"]["average_clv"] == 0.5
