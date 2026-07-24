import json
from pathlib import Path

from backtesting.config import BacktestConfig, PredictionMode
from backtesting.game_matching import match_game
from backtesting.historical_provider import HistoricalSnapshotProvider
from backtesting.markets import normalize_market
from backtesting.replay_engine import ReplayEngine
from backtesting.snapshots import normalize_dataset, snapshot_week_dir, validate_snapshot
from nfl_providers import normalize_odds_events

FIXTURE = Path(__file__).parent / "fixtures" / "historical_odds_sample.json"


def game():
    return {"game_id":"espn-401","league":"nfl","season":"2025","week":1,"kickoff_time":"2025-09-07T17:00:00Z","home_team":"BUF","away_team":"MIA","venue":"Stadium","status":"final"}


def outcome():
    return {"game_id":"espn-401","final_home_score":24,"final_away_score":17,"player_results":{},"market_results":{"h2h":"home"},"completed_at":"2025-09-07T20:30:00Z","source":"espn","captured_at":"2025-09-07T20:30:00Z","data_as_of":"2025-09-07T20:30:00Z","is_pregame":False,"season":"2025","week":1}


def test_historical_odds_payload_normalizes_all_team_markets_and_bookmaker_fields():
    payload = json.loads(FIXTURE.read_text())
    rows = normalize_odds_events(payload["data"], [game()])
    assert {r["market"] for r in rows} == {"h2h", "spread", "total"}
    assert all(r["game_id"] == "espn-401" for r in rows)
    assert all(r["sportsbook"] == "DraftKings" and r["bookmaker"] == "draftkings" for r in rows)
    assert {r["odds"] for r in rows} >= {-125, 105, -110, -108, -112}
    assert [r for r in rows if r["market"] == "h2h"][0]["line"] == 0
    assert [r for r in rows if r["market"] == "spread"][0]["line"] == -2.5


def test_market_normalization_accepts_odds_api_market_names():
    assert normalize_market("h2h") == "h2h"
    assert normalize_market("spreads") == "spread"
    assert normalize_market("totals") == "total"


def test_match_game_reports_provider_id_and_datetime_team_diagnostics():
    games = [game() | {"the_odds_api_event_id": "odds-api-evt-1"}]
    assert match_game({"id":"odds-api-evt-1"}, games).strategy == "provider_game_id"
    diag = match_game({"id":"different","commence_time":"2025-09-07T18:00:00Z","home_team":"Buffalo Bills","away_team":"Miami Dolphins"}, [game()], league="nfl")
    assert diag.matched and diag.game_id == "espn-401" and diag.strategy == "datetime_home_away_league"
    failed = match_game({"id":"x","commence_time":"2025-09-09T18:00:00Z","home_team":"Bills","away_team":"Dolphins"}, [game()], league="nfl")
    assert not failed.matched and failed.reasons


def write_snapshot(root):
    wdir = snapshot_week_dir(root, "nfl", "2025", 1)
    wdir.mkdir(parents=True)
    odds = normalize_dataset("odds", normalize_odds_events(json.loads(FIXTURE.read_text())["data"], [game()]), "nfl", "2025", 1)
    datasets = {
        "games": [game() | {"source":"espn","captured_at":"2025-09-06T17:00:00Z","data_as_of":"2025-09-06T17:00:00Z","is_pregame":True}],
        "odds": odds,
        "weather": [{"game_id":"espn-401","captured_at":"2025-09-06T17:00:00Z","temperature":70,"wind_speed":4,"precipitation":0,"conditions":"clear","source":"fixture","data_as_of":"2025-09-06T17:00:00Z","is_pregame":True,"season":"2025","week":1}],
        "injuries": [],
        "player_stats": [],
        "team_stats": [],
        "outcomes": [outcome()],
    }
    for name, rows in datasets.items():
        (wdir / f"{name}.json").write_text(json.dumps(rows, indent=2) + "\n")


def test_snapshot_validation_accepts_normalized_historical_odds(tmp_path):
    write_snapshot(tmp_path)
    report = validate_snapshot(tmp_path, "nfl", "2025", [1])
    assert report.ok, report.errors


def test_replay_ingests_validated_snapshot_in_betting_and_statistical_modes(tmp_path):
    write_snapshot(tmp_path)
    provider = HistoricalSnapshotProvider(tmp_path)
    def factory(p, c, w):
        odds = p.get_odds(c.league, c.season, w)
        return [{"game":"espn-401","market":odds[0]["market"],"prediction":odds[0]["selection"],"confidence":60,"sportsbook_odds":odds[0]["odds"],"sportsbook":odds[0]["sportsbook"]}]
    betting = BacktestConfig(league="nfl", season="2025", start_week=1, end_week=1, prediction_mode=PredictionMode.BETTING, data_dir=tmp_path, db_path=tmp_path / "betting.db", results_dir=tmp_path / "results")
    stat = BacktestConfig(league="nfl", season="2025", start_week=1, end_week=1, prediction_mode=PredictionMode.STATISTICAL, data_dir=tmp_path, db_path=tmp_path / "stat.db", results_dir=tmp_path / "results2")
    assert ReplayEngine(betting, provider=provider, prediction_factory=factory).run()["mode"] == "BETTING"
    assert ReplayEngine(stat, provider=provider, prediction_factory=factory).run()["mode"] == "STATISTICAL"
