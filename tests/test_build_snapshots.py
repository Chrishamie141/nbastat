import json
from argparse import Namespace
from pathlib import Path

import pytest

from backtesting.build_snapshots import main, parse_args
from backtesting.config import BacktestConfig
from backtesting.replay_engine import ReplayEngine
from backtesting.snapshot_sources import RawCache
from backtesting.snapshots import SnapshotError, validate_snapshot


class FakeSource:
    name = "fake"
    supported_datasets = {"games", "odds", "weather", "injuries", "player_stats", "team_stats", "outcomes"}

    def fetch_games(self, league, season, week, week_range):
        return [{"game_id":"fake-g1","league":league,"season":season,"week":week,"kickoff_time":"2025-09-07T17:00:00Z","home_team":"BUF","away_team":"MIA","venue":"Fake Stadium","status":"final","players":[{"player":"Test Quarterback","team":"BUF","position":"QB","opponent":"MIA","home_away":"home"}]}]

    def fetch_odds(self, league, season, week, week_range, games):
        return [{"game_id":"fake-g1","market":"PASS_YDS","selection":"Test Quarterback","line":250.5,"odds":-110,"sportsbook":"fake-book","captured_at":"2025-09-07T12:00:00Z"}]

    def fetch_weather(self, league, season, week, week_range, games):
        return [{"game_id":"fake-g1","captured_at":"2025-09-07T12:00:00Z","temperature":70,"wind_speed":4,"precipitation":0,"conditions":"clear"}]

    def fetch_injuries(self, league, season, week, week_range, games):
        return [{"team":"BUF","player":"Reserve","position":"WR","status":"out","captured_at":"2025-09-07T12:00:00Z"}]

    def fetch_player_stats(self, league, season, week, week_range, games):
        return [{"player":"Test Quarterback","team":"BUF","season":season,"through_week":0,"stats":{"pass_yards_per_game":255}}]

    def fetch_team_stats(self, league, season, week, week_range, games):
        return [{"team":"BUF","season":season,"through_week":0,"stats":{"points_per_game":24}}]

    def fetch_outcomes(self, league, season, week, week_range, games):
        return [{"game_id":"fake-g1","final_home_score":24,"final_away_score":17,"player_results":{"Test Quarterback":{"PASS_YDS":270}},"market_results":{"moneyline":"home"},"completed_at":"2025-09-07T20:30:00Z"}]


class FailingSource(FakeSource):
    def fetch_odds(self, league, season, week, week_range, games):
        from backtesting.snapshot_sources import ProviderUnavailable
        raise ProviderUnavailable("TOKEN-SECRET failed")


def args(tmp_path, **kw):
    defaults = dict(league="nfl", season="2025", start_week=1, end_week=1, data_dir=tmp_path / "snapshots", overwrite=False, resume=False, validate=True, dry_run=False, providers="fake", strict=False)
    defaults.update(kw)
    return Namespace(**defaults)


def test_cli_argument_parsing():
    ns = parse_args(["--league","nfl","--season","2025","--start-week","1","--end-week","18","--overwrite","--resume","--validate","--dry-run","--providers","local-json","--strict"])
    assert ns.league == "nfl" and ns.end_week == 18 and ns.strict and ns.providers == "local-json"


def test_fake_provider_creates_week_folder_and_valid_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr("backtesting.build_snapshots.create_sources", lambda spec: [FakeSource()])
    assert main(args(tmp_path)) == 0
    assert (tmp_path / "snapshots/nfl/2025/week_01/games.json").exists()
    assert validate_snapshot(tmp_path / "snapshots", "nfl", "2025", [1]).ok


def test_resume_skips_complete_valid_week(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("backtesting.build_snapshots.create_sources", lambda spec: [FakeSource()])
    assert main(args(tmp_path)) == 0
    assert main(args(tmp_path, resume=True)) == 0
    assert "skipped complete valid" in capsys.readouterr().out


def test_overwrite_protection(tmp_path, monkeypatch):
    monkeypatch.setattr("backtesting.build_snapshots.create_sources", lambda spec: [FakeSource()])
    assert main(args(tmp_path)) == 0
    assert main(args(tmp_path, validate=False)) == 1


def test_dry_run_does_not_write(tmp_path, monkeypatch):
    monkeypatch.setattr("backtesting.build_snapshots.create_sources", lambda spec: [FakeSource()])
    assert main(args(tmp_path, dry_run=True, validate=False)) == 0
    assert not (tmp_path / "snapshots").exists()


def test_provider_failure_handling_and_no_api_key_in_logs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("THE_ODDS_API_KEY", "TOKEN-SECRET")
    monkeypatch.setattr("backtesting.build_snapshots.create_sources", lambda spec: [FailingSource()])
    assert main(args(tmp_path, strict=False)) == 0
    out = capsys.readouterr().out
    assert "TOKEN-SECRET" not in out
    assert "failed" in out


def test_raw_response_caching(tmp_path):
    calls = {"n": 0}
    cache = RawCache(tmp_path / "raw")
    def fetch():
        calls["n"] += 1
        return [{"ok": True}]
    assert cache.get_or_fetch("fake", "nfl", "2025", 1, "games", fetch) == [{"ok": True}]
    assert cache.get_or_fetch("fake", "nfl", "2025", 1, "games", fetch) == [{"ok": True}]
    assert calls["n"] == 1


def test_future_data_leakage_validation(tmp_path, monkeypatch):
    monkeypatch.setattr("backtesting.build_snapshots.create_sources", lambda spec: [FakeSource()])
    assert main(args(tmp_path)) == 0
    odds = tmp_path / "snapshots/nfl/2025/week_01/odds.json"
    data = json.loads(odds.read_text())
    data[0]["captured_at"] = "2025-09-07T18:00:00Z"
    odds.write_text(json.dumps(data))
    report = validate_snapshot(tmp_path / "snapshots", "nfl", "2025", [1])
    assert not report.ok
    assert any("Future-data leakage" in e for e in report.errors)


def test_generated_snapshot_runs_replay_engine(tmp_path, monkeypatch):
    monkeypatch.setattr("backtesting.build_snapshots.create_sources", lambda spec: [FakeSource()])
    assert main(args(tmp_path)) == 0
    config = BacktestConfig(league="nfl", season="2025", start_week=1, end_week=1, markets=("PASS_YDS",), db_path=tmp_path / "bt.db", data_dir=tmp_path / "snapshots", results_dir=tmp_path / "results")
    summary = ReplayEngine(config).run()
    assert summary["metrics"]["total_predictions"] > 0
    assert summary["metrics"]["graded_predictions"] > 0
    assert summary["metrics"]["overall_accuracy"] is not None
