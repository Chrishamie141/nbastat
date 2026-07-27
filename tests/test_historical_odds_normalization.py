import json
from pathlib import Path

from backtesting.config import BacktestConfig, PredictionMode
from backtesting.game_matching import match_game, normalize_team, parse_dt
from backtesting.historical_provider import HistoricalSnapshotProvider
from backtesting.markets import normalize_market
from backtesting.replay_engine import ReplayEngine
from backtesting.snapshots import normalize_dataset, snapshot_week_dir, validate_snapshot
from backtesting.snapshot_sources import TheOddsApiSnapshotSource
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
    known = next(r for r in rows if r["game_id"] == "espn-401" and r["market"] == "spread" and r["selection"] == "Buffalo Bills")
    assert (known["line"], known["odds"], known["sportsbook"], known["captured_at"]) == (-2.5, -110, "DraftKings", "2025-09-06T17:00:00Z")


def test_market_normalization_accepts_odds_api_market_names():
    assert normalize_market("h2h") == "h2h"
    assert normalize_market("moneyline") == "h2h"
    assert normalize_market("moneylines") == "h2h"
    assert normalize_market("spread") == "spread"
    assert normalize_market("spreads") == "spread"
    assert normalize_market("total") == "total"
    assert normalize_market("totals") == "total"
    assert BacktestConfig(league="nfl", season="2025", markets=("moneylines", "spreads", "totals")).normalized_markets() == ("h2h", "spread", "total")


def test_player_market_normalization_is_case_insensitive_and_canonical():
    for value in ("pass_yds", "PASS_YDS", "Pass_Yds"):
        assert normalize_market(value) == "PASS_YDS"
    assert BacktestConfig(
        league="nfl", season="2025", markets=("rush_yds", "Rec_Yds", "receptions", "pass_td")
    ).normalized_markets() == ("RUSH_YDS", "REC_YDS", "RECEPTIONS", "PASS_TD")


def test_match_game_reports_provider_id_and_datetime_team_diagnostics():
    games = [game() | {"the_odds_api_event_id": "odds-api-evt-1"}]
    assert match_game({"id":"odds-api-evt-1"}, games).strategy == "provider_game_id"
    diag = match_game({"id":"different","commence_time":"2025-09-07T18:00:00Z","home_team":"Buffalo Bills","away_team":"Miami Dolphins"}, [game()], league="nfl")
    assert diag.matched and diag.game_id == "espn-401" and diag.strategy == "datetime_home_away_league"
    failed = match_game({"id":"x","commence_time":"2025-09-09T18:00:00Z","home_team":"Bills","away_team":"Dolphins"}, [game()], league="nfl")
    assert not failed.matched and failed.reasons


def test_all_nfl_team_name_styles_and_abbreviations_are_canonical():
    assert normalize_team(" LA Rams ") == "LAR"
    assert normalize_team("N.Y. Jets") == "NYJ"
    assert normalize_team("GNB") == "GB"
    assert normalize_team("Jacksonville Jaguars") == "JAX"
    assert normalize_team("Bucs") == "TB"
    assert normalize_team("Washington Football Team") == "WAS"


def test_timestamp_normalization_and_safe_kickoff_tolerance():
    assert parse_dt("2025-09-07T13:00:00-04:00") == parse_dt("2025-09-07T17:00:00Z")
    assert parse_dt("2025-09-07T17:00:00") == parse_dt("2025-09-07T17:00:00Z")
    alias_event = {"id":"provider-id-does-not-match","sport_key":"americanfootball_nfl","commence_time":"2025-09-07T13:02:00-04:00","home_team":"Buffalo Bills","away_team":"Miami Dolphins"}
    assert match_game(alias_event, [game()], league="nfl", tolerance_minutes=5).game_id == "espn-401"
    outside = alias_event | {"commence_time":"2025-09-07T13:06:00-04:00"}
    diag = match_game(outside, [game()], league="nfl", tolerance_minutes=5)
    assert not diag.matched
    assert diag.closest_game_id == "espn-401"
    assert "kickoff_datetime_outside_tolerance" in diag.reasons


def test_event_level_match_propagates_game_id_and_discards_unmatched_by_default(capsys):
    payload = json.loads(FIXTURE.read_text())["data"][0]
    payload["home_team"] = "Buffalo Bills"
    payload["away_team"] = "Miami Dolphins"
    unrelated = {
        "id":"unrelated-event", "sport_key":"americanfootball_nfl",
        "commence_time":"2025-09-14T17:00:00Z", "home_team":"LA Rams",
        "away_team":"NY Jets", "bookmakers": payload["bookmakers"] * 2,
    }
    diagnostics = {}
    rows = normalize_odds_events([payload, unrelated], [game()], diagnostics=diagnostics)
    assert len(rows) == 6
    assert {row["game_id"] for row in rows} == {"espn-401"}
    assert all(row["event_id"] == "odds-api-evt-1" for row in rows)
    output = capsys.readouterr().out
    assert output == ""
    assert diagnostics == {
        "provider_events_received": 2,
        "provider_events_matched": 1,
        "provider_events_discarded": 1,
        "odds_rows_persisted": 6,
    }


def test_unmatched_details_require_debug_flag(capsys):
    payload = json.loads(FIXTURE.read_text())["data"][0]
    unrelated = payload | {"id": "week-2", "commence_time": "2025-09-14T17:00:00Z"}
    normalize_odds_events([unrelated], [game()], debug=True)
    output = capsys.readouterr().out
    assert output.count("Unmatched Odds API event:") == 1
    assert "provider_event_id=week-2" in output


def test_per_game_historical_request_retains_only_intended_canonical_event(capsys):
    template = json.loads(FIXTURE.read_text())["data"][0]
    intended = game()
    other_week_one = intended | {"game_id": "espn-402", "kickoff_time": "2025-09-08T00:00:00Z", "home_team": "KC", "away_team": "DEN"}
    events = [template]
    for event_id, kickoff, home, away in (
        ("other-week-1", "2025-09-08T00:00:00Z", "Kansas City Chiefs", "Denver Broncos"),
        ("week-2", "2025-09-14T17:00:00Z", "Los Angeles Rams", "Tennessee Titans"),
        ("week-3", "2025-09-21T17:00:00Z", "Seattle Seahawks", "New Orleans Saints"),
        ("december", "2025-12-07T17:00:00Z", "Pittsburgh Steelers", "Minnesota Vikings"),
    ):
        events.append(template | {"id": event_id, "commence_time": kickoff, "home_team": home, "away_team": away})

    class FixtureProvider:
        def fetch_odds(self, season, week, games, snapshot_time=None):
            self.last_diagnostics = {}
            return normalize_odds_events(events, games, diagnostics=self.last_diagnostics)

    source = TheOddsApiSnapshotSource.__new__(TheOddsApiSnapshotSource)
    source.provider = FixtureProvider()
    source.hours_before_kickoff = 24
    rows = source.fetch_odds("nfl", "2025", 1, ("2025-09-04", "2025-09-10"), [intended])
    assert len(rows) == 6
    assert {row["game_id"] for row in rows} == {"espn-401"}
    assert {row["event_id"] for row in rows} == {"odds-api-evt-1"}
    assert all(row["provider_event_matched"] is True for row in rows)
    output = capsys.readouterr().out
    assert "provider_events_received=5" in output
    assert "provider_events_matched=1" in output
    assert "provider_events_discarded=4" in output


def test_full_week_fixture_odds_ids_equal_sixteen_canonical_game_ids():
    template = json.loads(FIXTURE.read_text())["data"][0]
    games = []
    for index in range(16):
        games.append(game() | {
            "game_id": f"espn-{index}",
            "kickoff_time": f"2025-09-{7 + index // 8:02d}T{13 + index % 8:02d}:00:00Z",
            "home_team": f"HOME{index}", "away_team": f"AWAY{index}",
        })

    class FixtureProvider:
        def fetch_odds(self, season, week, requested, snapshot_time=None):
            target = requested[0]
            intended = template | {
                "id": f"provider-{target['game_id']}", "commence_time": target["kickoff_time"],
                "home_team": target["home_team"], "away_team": target["away_team"],
            }
            later = template | {"id": f"later-{target['game_id']}", "commence_time": "2025-12-20T17:00:00Z"}
            self.last_diagnostics = {}
            return normalize_odds_events([intended, later], requested, diagnostics=self.last_diagnostics)

    source = TheOddsApiSnapshotSource.__new__(TheOddsApiSnapshotSource)
    source.provider = FixtureProvider()
    source.hours_before_kickoff = 24
    odds = source.fetch_odds("nfl", "2025", 1, ("2025-09-04", "2025-09-10"), games)
    assert {row["game_id"] for row in odds} == {game["game_id"] for game in games}


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


def test_validation_rejects_duplicate_unproven_or_post_kickoff_odds(tmp_path):
    write_snapshot(tmp_path)
    odds_path = snapshot_week_dir(tmp_path, "nfl", "2025", 1) / "odds.json"
    odds = json.loads(odds_path.read_text())
    odds[0]["provider_event_matched"] = False
    odds[0]["data_as_of"] = "2025-09-07T17:00:00Z"
    odds.append(dict(odds[0]))
    odds_path.write_text(json.dumps(odds, indent=2) + "\n")
    report = validate_snapshot(tmp_path, "nfl", "2025", [1])
    assert not report.ok
    assert any("unreconciled_provider_event" in error for error in report.errors)
    assert any("duplicate_odds_row" in error for error in report.errors)
    assert any("data_as_of at/after kickoff" in error for error in report.errors)


def test_validation_groups_unmatched_bookmaker_rows_by_provider_event(tmp_path):
    write_snapshot(tmp_path)
    odds_path = snapshot_week_dir(tmp_path, "nfl", "2025", 1) / "odds.json"
    odds = json.loads(odds_path.read_text())
    base = odds[0] | {"game_id":"unmatched-provider-id", "event_id":"one-event"}
    odds.extend([base | {"sportsbook":"Book A"}, base | {"sportsbook":"Book B"}])
    odds_path.write_text(json.dumps(odds, indent=2) + "\n")
    report = validate_snapshot(tmp_path, "nfl", "2025", [1])
    matching_errors = [error for error in report.errors if "Odds without matching game" in error]
    assert matching_errors == [
        "Odds without matching game for NFL 2025 Week 1: event=one-event; affected_rows=2"
    ]


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
