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
        ("outcomes", 1),
        ("games", 2),
        ("odds", 2),
        ("outcomes", 2),
    ]


def test_grader_supports_over_under_push_and_moneyline():
    grader = PredictionGrader()
    assert grader.grade({"market": "player_prop", "prediction": "over", "line": 10}, {"actual_result": 12})["correct"] is True
    assert grader.grade({"market": "player_prop", "prediction": "under", "line": 10}, {"actual_result": 10})["grade"] == "push"
    assert grader.grade({"market": "moneyline", "prediction": "away"}, {"actual_result": "home"})["correct"] is False
