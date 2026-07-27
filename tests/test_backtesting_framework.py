from argparse import Namespace
from pathlib import Path

from backtesting.config import BacktestConfig, PredictionMode
from backtesting.grader import PredictionGrader
from backtesting.historical_provider import HistoricalSnapshotProvider
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


def test_historical_provider_allows_prior_season_but_filters_same_week_stats(tmp_path):
    week_dir = tmp_path / "nfl" / "2025" / "week_01"
    week_dir.mkdir(parents=True)
    rows = [
        {"player": "Prior", "season": "2024", "through_week": 18, "record_role": "pregame_history", "is_pregame": True},
        {"player": "Leaked", "season": "2025", "through_week": 1, "record_role": "pregame_history", "is_pregame": True},
    ]
    (week_dir / "player_stats.json").write_text(__import__("json").dumps(rows))
    assert [row["player"] for row in HistoricalSnapshotProvider(tmp_path).get_player_stats("nfl", "2025", 1)] == ["Prior"]


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
    assert summary["evaluation"]["totals"] == {
        "games_evaluated": 2, "markets_evaluated": 2,
        "candidates_evaluated": 2, "bets_accepted": 2,
    }
    assert set(summary["evaluation"]["weeks"]) == {"1", "2"}


def test_grader_supports_over_under_push_and_moneyline():
    grader = PredictionGrader()
    assert grader.grade({"market": "player_prop", "prediction": "over", "line": 10}, {"actual_result": 12})["correct"] is True
    assert grader.grade({"market": "player_prop", "prediction": "under", "line": 10}, {"actual_result": 10})["grade"] == "push"
    assert grader.grade({"market": "moneyline", "prediction": "away"}, {"actual_result": "home"})["correct"] is False


def test_grader_supports_team_markets_win_loss_and_push():
    grader = PredictionGrader()
    final = {"final_home_score": 24, "final_away_score": 20, "home_team": "BUF", "away_team": "MIA", "actual_result": "home"}
    assert grader.grade({"market": "h2h", "prediction": "home"}, final)["grade"] == "win"
    assert grader.grade({"market": "h2h", "prediction": "away"}, final)["grade"] == "loss"
    assert grader.grade({"market": "spread", "prediction": "BUF", "line": -3.5}, final)["grade"] == "win"
    assert grader.grade({"market": "spread", "prediction": "MIA", "line": 3.5}, final)["grade"] == "loss"
    assert grader.grade({"market": "spread", "prediction": "BUF", "line": -4}, final)["grade"] == "push"
    assert grader.grade({"market": "total", "prediction": "over", "line": 43.5}, final)["grade"] == "win"
    assert grader.grade({"market": "total", "prediction": "under", "line": 43.5}, final)["grade"] == "loss"
    assert grader.grade({"market": "total", "prediction": "over", "line": 44}, final)["grade"] == "push"


def test_team_market_replay_generates_candidates_before_threshold_acceptance(tmp_path):
    class TeamMarketProvider(StubProvider):
        def get_games(self, league, season, week):
            return [{"game_id": "game-1", "home_team": "BUF", "away_team": "MIA", "kickoff_time": "2025-09-07T17:00:00Z"}]

        def get_odds(self, league, season, week):
            return [
                {"game_id": "game-1", "market": market, "selection": selection, "line": line, "odds": -110, "sportsbook": "fixture", "captured_at": "2025-09-07T12:00:00Z", "snapshot_timestamp": "2025-09-07T12:00:00Z"}
                for market, selection, line in (("h2h", "BUF", 0), ("spread", "BUF", -2.5), ("total", "Over", 45.5))
            ]

        def get_team_stats(self, league, season, week):
            return [{"team": team, "season": "2024", "through_week": 18, "stats": {"points_per_game": 24}} for team in ("BUF", "MIA")]

        def get_outcomes(self, league, season, week):
            return [{"game_id": "game-1", "final_home_score": 24, "final_away_score": 20, "market_results": {"h2h": "BUF", "spread": 4, "total": 44}}]

    config = BacktestConfig(league="nfl", season="2025", start_week=1, end_week=1, markets=("moneylines", "spreads", "totals"), export=False, db_path=tmp_path / "team.db", data_dir=tmp_path)
    summary = ReplayEngine(config, provider=TeamMarketProvider()).run()
    evaluation = summary["evaluation"]
    assert evaluation["games_evaluated"] == 1
    assert evaluation["markets_evaluated"] == 3
    assert evaluation["candidates_evaluated"] == 3
    assert evaluation["bets_accepted"] == 0
    assert evaluation["no_bet_reasons"] == {"edge_below_threshold": 3}
    assert len(evaluation["games"][0]["market_decisions"]) == 3
    assert all(row["model_probability"] is not None for row in evaluation["games"][0]["market_decisions"])


def test_offline_week_one_shape_evaluates_all_games_and_explains_every_no_bet(tmp_path):
    """Regression for the validated 16-game/1,042-odds snapshot shape; no network or model fallback."""
    class WeekOneProvider(StubProvider):
        games = [{"game_id": f"espn-{i}", "home_team": f"H{i}", "away_team": f"A{i}", "kickoff_time": "2025-09-07T17:00:00Z"} for i in range(16)]
        base_odds = [
            {"game_id": game["game_id"], "market": market, "selection": selection, "line": line, "odds": -110, "sportsbook": f"Book-{book}", "captured_at": "2025-09-06T17:00:00Z", "snapshot_timestamp": "2025-09-06T17:00:00Z"}
            for game in games for book in range(11) for market, selection, line in (("h2h", game["home_team"], 0), ("spread", game["home_team"], -2.5), ("total", "Over", 45.5))
        ]
        odds = (base_odds * 2)[:1042]

        def get_games(self, *args): return self.games
        def get_odds(self, *args): return self.odds
        def get_player_stats(self, *args): return []
        def get_team_stats(self, *args):
            return [{"team": team, "season": "2024", "through_week": 18, "stats": {"points_per_game": 24}} for game in self.games for team in (game["home_team"], game["away_team"])]
        def get_outcomes(self, *args):
            return [{"game_id": game["game_id"], "final_home_score": 24, "final_away_score": 20, "market_results": {"h2h": game["home_team"], "spread": 4, "total": 44}} for game in self.games]

    provider = WeekOneProvider()
    assert len(provider.get_games()) == 16 and len(provider.get_odds()) == 1042 and len(provider.get_team_stats()) == 32
    config = BacktestConfig(league="nfl", season="2025", start_week=1, end_week=1, markets=("h2h", "spreads", "totals"), export=False, db_path=tmp_path / "week1.db", data_dir=tmp_path)
    summary = ReplayEngine(config, provider=provider).run()
    evaluation = summary["evaluation"]
    assert (evaluation["games_evaluated"], evaluation["markets_evaluated"], evaluation["candidates_evaluated"], evaluation["bets_accepted"]) == (16, 48, 48, 0)
    assert evaluation["no_bet_reasons"] == {"edge_below_threshold": 48}
    assert all(game["rejection_reasons"] and game["team_stats_available"] == 2 for game in evaluation["games"])

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


def test_player_replay_and_diagnostics_allow_missing_optional_team_stats(tmp_path):
    src = Path("tests/fixtures/backtesting")
    data_dir = tmp_path / "snapshots"
    shutil.copytree(src, data_dir)
    (data_dir / "nfl" / "2025" / "week_01" / "team_stats.json").unlink()
    config = BacktestConfig(
        league="nfl", season="2025", start_week=1, end_week=1,
        markets=("pass_yds",), export=False, db_path=tmp_path / "optional.db", data_dir=data_dir,
    )
    summary = ReplayEngine(config).run()
    assert summary["metrics"]["total_predictions"] > 0
    week = summary["evaluation"]["weeks"]["1"]
    assert week["games_evaluated"] == 1
    assert week["games"][0]["team_stats_available"] == 0


def test_model_that_requires_team_stats_can_require_snapshot_explicitly(tmp_path):
    provider = HistoricalSnapshotProvider(tmp_path)

    def game_model(provider, config, week):
        provider.get_team_stats(config.league, config.season, week)
        return []

    config = BacktestConfig(
        league="nfl", season="2025", start_week=1, end_week=1,
        export=False, db_path=tmp_path / "required.db", data_dir=tmp_path,
        prediction_mode=PredictionMode.STATISTICAL,
    )
    # Games/odds are loaded by the engine before the factory, so make only
    # those prerequisites present; the model's explicit team-history load is strict.
    week_dir = tmp_path / "nfl" / "2025" / "week_01"
    week_dir.mkdir(parents=True)
    (week_dir / "games.json").write_text("[]")
    (week_dir / "odds.json").write_text("[]")
    with pytest.raises(SnapshotError, match="No team_stats snapshot"):
        ReplayEngine(config, provider=provider, prediction_factory=game_model).run()


def test_mixed_case_player_market_filter_matches_predictor_and_snapshot(tmp_path):
    src = Path("tests/fixtures/backtesting")
    data_dir = tmp_path / "snapshots"
    shutil.copytree(src, data_dir)
    config = BacktestConfig(
        league="nfl", season="2025", start_week=1, end_week=1,
        markets=("Pass_Yds",), export=False, db_path=tmp_path / "case.db", data_dir=data_dir,
    )
    summary = ReplayEngine(config).run()
    assert summary["metrics"]["total_predictions"] > 0
    assert summary["evaluation"]["totals"]["bets_accepted"] > 0


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
    assert summary["evaluation"]["weeks"]["1"]["no_bet_reasons"] == {"missing_historical_odds": 1}


def test_statistical_mode_allows_predictions_without_odds(tmp_path):
    provider = NoOddsProvider()
    config = BacktestConfig(league="nfl", season="2025", start_week=1, end_week=1, export=False, db_path=tmp_path/"s.db", data_dir=tmp_path, prediction_mode=PredictionMode.STATISTICAL)
    engine = ReplayEngine(config, provider=provider, prediction_factory=lambda p, c, w: [{"game":"game-1","market":"moneyline","prediction":"home","confidence":70}])
    summary = engine.run()
    assert summary["mode"] == "STATISTICAL"
    assert summary["metrics"]["graded_predictions"] == 1
    assert summary["evaluation"]["totals"]["bets_accepted"] == 1


def test_roi_uses_american_odds_profit(tmp_path):
    provider = StubProvider()
    config = BacktestConfig(league="nfl", season="2025", start_week=1, end_week=1, export=False, db_path=tmp_path/"r.db", data_dir=tmp_path)
    pred = {"game":"game-1","market":"moneyline","prediction":"home","confidence":70,"sportsbook_odds":100,"edge":0.05,"clv":0.5}
    summary = ReplayEngine(config, provider=provider, prediction_factory=lambda p, c, w: [pred]).run()
    assert summary["metrics"]["roi"] == 1.0
    assert summary["metrics"]["average_edge"] == 0.05
    assert summary["metrics"]["average_clv"] == 0.5


def test_game_model_is_leakage_safe_and_best_price_is_deduplicated(tmp_path):
    class Provider(StubProvider):
        def get_games(self, *args):
            return [{"game_id": "g", "home_team": "BUF", "away_team": "MIA", "kickoff_time": "2025-09-07T17:00:00Z"}]
        def get_team_stats(self, *args):
            return [
                {"team": "BUF", "season": 2024, "through_week": 18, "data_as_of": "2025-08-01T00:00:00Z", "stats": {"points_per_game": 30, "points_allowed_per_game": 18}},
                {"team": "MIA", "season": 2024, "through_week": 18, "data_as_of": "2025-08-01T00:00:00Z", "stats": {"points_per_game": 18, "points_allowed_per_game": 28}},
                {"team": "BUF", "season": 2025, "through_week": 1, "data_as_of": "2025-09-08T00:00:00Z", "stats": {"points_per_game": 0}},
            ]
        def get_odds(self, *args):
            return [
                {"game_id":"g","market":"moneyline","selection":"BUF","line":0,"odds":100,"sportsbook":"worse","captured_at":"2025-09-07T12:00:00Z"},
                {"game_id":"g","market":"h2h","selection":"BUF","line":0,"odds":120,"sportsbook":"best","captured_at":"2025-09-07T12:00:00Z"},
                {"game_id":"g","market":"spreads","selection":"BUF","line":-3.5,"odds":110,"sportsbook":"best","captured_at":"2025-09-07T12:00:00Z"},
                {"game_id":"g","market":"totals","selection":"Over","line":42.5,"odds":110,"sportsbook":"best","captured_at":"2025-09-07T12:00:00Z"},
            ]
        def get_player_stats(self,*args): return []
        def get_outcomes(self,*args):
            return [{"game_id":"g","final_home_score":28,"final_away_score":17,"market_results":{"h2h":"BUF","spread":11,"total":45}}]
    config=BacktestConfig(league="nfl",season="2025",start_week=1,end_week=1,markets=("h2h","spread","total"),export=False,db_path=tmp_path/"game.db",data_dir=tmp_path)
    summary=ReplayEngine(config,provider=Provider()).run()
    rows=ReplayEngine(config,provider=Provider()).store.load_predictions(summary["run_id"])
    assert len({(r["game"],r["market"],r["prediction"]) for r in rows}) == len(rows)
    h2h=next(r for r in rows if r["market"] == "h2h")
    assert h2h["sportsbook"] == "best" and h2h["sportsbook_odds"] == 120
    assert h2h["implied_probability"] == pytest.approx(100/220)
    assert h2h["edge"] == pytest.approx(h2h["model_probability"]-h2h["implied_probability"])
    assert summary["metrics"]["graded_predictions"] == len(rows)


def test_team_markets_reject_missing_or_future_history(tmp_path):
    class Provider(StubProvider):
        def get_games(self,*args): return [{"game_id":"g","home_team":"BUF","away_team":"MIA","kickoff_time":"2025-09-07T17:00:00Z"}]
        def get_odds(self,*args): return [{"game_id":"g","market":"h2h","selection":"BUF","odds":120,"sportsbook":"book","captured_at":"2025-09-07T12:00:00Z"}]
        def get_team_stats(self,*args): return [{"team":"BUF","season":2024,"through_week":18,"data_as_of":"2025-09-08T00:00:00Z","stats":{"points_per_game":30}}]
        def get_player_stats(self,*args): return []
        def get_outcomes(self,*args): return []
    config=BacktestConfig(league="nfl",season="2025",start_week=1,end_week=1,markets=("moneyline",),export=False,db_path=tmp_path/"missing.db",data_dir=tmp_path)
    evaluation=ReplayEngine(config,provider=Provider()).run()["evaluation"]
    assert evaluation["candidates_evaluated"] == 0
    assert evaluation["no_bet_reasons"] == {"insufficient_pregame_history": 1}
