from __future__ import annotations

from datetime import date
import json

import pytest
from fastapi.testclient import TestClient

from backend.app.services.nba.cold_start import build_cold_start_features, build_player_cold_start_features
from backend.app.services.nba.grading import grade_prediction
from backend.app.services.nba.lifecycle import (
    append_market_observation, foundation_status, freeze_prediction, settle_game, upsert_game,
)
from backend.app.services.nba.model import NBA_MODEL_VERSION, NBAPlayerStatModel
from backend.app.services.nba.replay import NBAReplayEngine
from backend.app.services.nba.schedule import ScheduleIngestionError, fetch_schedule
from backend.app.services.nba.season import active_nba_season, current_nba_season, normalize_nba_season
from backend.app.services.nba.registry import registry_status
from backtesting.nba_replay import replay_dataset


@pytest.fixture
def nba_db(tmp_path, monkeypatch):
    path = tmp_path / "nba-foundation.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL_NON_POOLING", raising=False)
    return path


def game(status="FINAL", home_score=110, away_score=100):
    return {"game_id": "espn-1", "season": "2026-27", "kickoff_time": "2026-10-20T23:00:00Z",
            "home_team": "BOS", "away_team": "NYK", "status": status,
            "home_score": home_score, "away_score": away_score, "provider": "espn",
            "provider_event_id": "1", "provider_timestamp": "2026-10-21T02:00:00Z"}


def prediction(**values):
    base = {"game_id": "espn-1", "season": "2026-27", "kickoff_time": "2026-10-20T23:00:00Z",
            "market": "moneyline", "selection": "BOS", "line": None,
            "model_probability": .61, "projection": None, "edge": .04, "market_odds": -125,
            "model_version": NBA_MODEL_VERSION, "data_version": "fixture-v1",
            "generated_at": "2026-10-20T20:00:00Z", "feature_cutoff": "2026-10-20T19:59:59Z",
            "source_provider": "fixture"}
    return {**base, **values}


def test_season_2026_27_and_model_are_nba_owned():
    season = normalize_nba_season("2026-27")
    assert (season.start_year, season.end_year, season.code) == (2026, 2027, "2026-27")
    assert NBAPlayerStatModel.__module__.endswith("services.nba.model")
    assert NBA_MODEL_VERSION.startswith("nba-")


def test_season_resolution_changes_at_july_boundary(monkeypatch):
    monkeypatch.delenv("NBA_ACTIVE_SEASON", raising=False)
    assert current_nba_season(date(2026, 6, 30)).code == "2025-26"
    assert current_nba_season(date(2026, 7, 1)).code == "2026-27"
    assert active_nba_season(today=date(2026, 7, 1)).code == "2026-27"
    monkeypatch.setenv("NBA_ACTIVE_SEASON", "2027-28")
    assert active_nba_season(today=date(2026, 7, 1)).code == "2027-28"
    assert normalize_nba_season("2026/2027").code == "2026-27"


def test_schedule_is_batched_and_deduplicated():
    calls = []
    event = {"id": "1", "date": "2026-10-20T23:00:00Z",
             "status": {"type": {"state": "pre", "detail": "Scheduled"}},
             "competitions": [{"competitors": [
                 {"homeAway": "home", "team": {"abbreviation": "BOS"}},
                 {"homeAway": "away", "team": {"abbreviation": "NYK"}},
             ]}]}
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"events": [event]}
    def get(url, params, timeout):
        calls.append(params["dates"]); return Response()
    rows = fetch_schedule(season="2026-27", start=date(2026, 9, 20), end=date(2026, 10, 20),
                          http_get=get)
    assert calls == ["202609", "202610"]
    assert len(rows) == 1 and rows[0]["season"] == "2026-27"


def test_schedule_retries_failed_month_and_never_silently_drops_it():
    calls = 0
    def fail(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        import requests
        raise requests.Timeout("fixture")
    with pytest.raises(ScheduleIngestionError, match="202610"):
        fetch_schedule(season="2026-27", start=date(2026, 10, 1), end=date(2026, 10, 2),
                       http_get=fail, max_attempts=2)
    assert calls == 2


def test_immutable_snapshot_and_market_boundary(nba_db):
    snapshot = freeze_prediction(prediction())
    assert freeze_prediction(prediction()) == snapshot
    later = freeze_prediction(prediction(generated_at="2026-10-20T21:00:00Z",
                                         feature_cutoff="2026-10-20T20:59:59Z",
                                         model_probability=.63))
    assert later != snapshot
    with pytest.raises(ValueError, match="before kickoff"):
        freeze_prediction(prediction(generated_at="2026-10-20T23:00:00Z"))
    observation = {"game_id": "espn-1", "season": "2026-27", "provider": "odds",
                   "sportsbook": "book", "market": "moneyline", "selection": "BOS",
                   "line": None, "odds": -125, "observed_at": "2026-10-20T22:59:59Z",
                   "kickoff_time": "2026-10-20T23:00:00Z"}
    assert append_market_observation(observation) == append_market_observation(observation)
    with pytest.raises(ValueError, match="strictly before kickoff"):
        append_market_observation({**observation, "observed_at": observation["kickoff_time"]})
    with pytest.raises(ValueError, match="provider and sportsbook"):
        append_market_observation({**observation, "sportsbook": ""})


def test_automatic_settlement_is_idempotent_and_push_is_correct(nba_db):
    upsert_game(game())
    freeze_prediction(prediction())
    freeze_prediction(prediction(market="total", selection="over", line=210.0))
    first = settle_game("espn-1")
    assert first == {"settled": 2, "pending": 0, "alreadySettled": 0}
    second = settle_game("espn-1")
    assert second["settled"] == 0 and second["alreadySettled"] == 2
    assert grade_prediction({"market": "total", "selection": "over", "line": 210}, game())["status"] == "PUSH"


def test_grading_uses_explicit_win_loss_push_and_unresolved_states():
    assert grade_prediction({"market": "moneyline", "selection": "BOS"}, game())["status"] == "WIN"
    assert grade_prediction({"market": "moneyline", "selection": "NYK"}, game())["status"] == "LOSS"
    assert grade_prediction({"market": "total", "selection": "under", "line": 210}, game())["status"] == "PUSH"
    assert grade_prediction({"market": "moneyline", "selection": "BOS"}, game("SCHEDULED", None, None))["status"] == "UNRESOLVED"


def test_missing_player_stat_remains_pending(nba_db):
    upsert_game(game())
    freeze_prediction(prediction(market="points", selection="over", line=24.5, player_name="Player"))
    result = settle_game("espn-1", player_stats={})
    assert result["pending"] == 1 and result["settled"] == 0


def test_chronological_replay_excludes_future_history_and_grades_push():
    seen = []
    def factory(game_row, history, version):
        seen.append([row["game_id"] for row in history])
        return [{"market": "total", "selection": "over", "line": 210,
                 "generated_at": "2026-10-20T22:00:00Z"}]
    summary = NBAReplayEngine(season="2026-27", model_version=NBA_MODEL_VERSION,
                              prediction_factory=factory).run(
        games=[game("SCHEDULED")],
        history=[{"game_id": "old", "completed_at": "2026-10-19T23:00:00Z"},
                 {"game_id": "future", "completed_at": "2026-10-21T23:00:00Z"}],
        outcomes=[game()])
    assert seen == [["old"]]
    assert summary["metrics"]["pushes"] == 1
    assert summary["metrics"]["validWagerCount"] == 0


def test_cold_start_blends_prior_and_current_without_future_rows():
    rows = [
        {"team": "BOS", "season": "2025-26", "completed_at": "2026-04-01T00:00:00Z", "pace": 100},
        {"team": "BOS", "season": "2026-27", "completed_at": "2026-10-20T00:00:00Z", "pace": 110},
        {"team": "BOS", "season": "2026-27", "completed_at": "2026-10-30T00:00:00Z", "pace": 999},
    ]
    result = build_cold_start_features(rows, team="BOS", cutoff="2026-10-25T00:00:00Z",
                                       current_season="2026-27", prior_season="2025-26")
    assert result["current_games"] == 1 and result["prior_games"] == 1
    assert result["features"]["pace"] == pytest.approx(101.0)


def test_player_cold_start_preserves_identity_roster_change_and_cutoff():
    rows = [
        {"player_id": "p1", "team_id": "A", "team": "A", "season": "2025-26", "completed_at": "2026-04-01T00:00:00Z", "points": 20},
        {"player_id": "p1", "team_id": "B", "team": "B", "season": "2026-27", "completed_at": "2026-10-20T00:00:00Z", "points": 24},
        {"player_id": "p1", "team_id": "B", "team": "B", "season": "2026-27", "completed_at": "2026-10-30T00:00:00Z", "points": 99},
    ]
    result = build_player_cold_start_features(rows, player_id="p1", cutoff="2026-10-25T00:00:00Z",
                                              current_season="2026-27", prior_season="2025-26")
    assert result["current_games"] == 1 and result["prior_games"] == 1
    assert result["rosterChangeStatus"] == "OBSERVED_MULTIPLE_TEAMS"
    assert result["features"]["points"] == pytest.approx(20.4)


def test_foundation_status_reports_isolated_store(nba_db):
    upsert_game(game("SCHEDULED", None, None))
    freeze_prediction(prediction())
    status = foundation_status("2026-27")
    assert status["modelIsolation"] == "NBA_ONLY"
    assert status["games"] == 1 and status["predictions"] == 1
    assert status["productionReady"] is False
    assert registry_status()["champion"] is None


def test_migration_keeps_nba_evidence_server_only_and_immutable():
    sql = open("supabase/migrations/20261007140555_nba_replay_foundation.sql", encoding="utf-8").read()
    assert "enable row level security" in sql.lower()
    assert "revoke all on table public.nba_prediction_snapshots from anon, authenticated" in sql.lower()
    assert "nba_prediction_snapshots_immutable_update" in sql
    extension = open("supabase/migrations/20261007173500_extend_nba_replay_governance.sql", encoding="utf-8").read().lower()
    assert "create table if not exists public.nba_model_registry" in extension
    assert "revoke all on table public.nba_model_registry from anon, authenticated" in extension
    assert "validation_status" in extension


def test_offline_replay_command_writes_a_deterministic_result(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "games.json").write_text(json.dumps([game("SCHEDULED")]), encoding="utf-8")
    (dataset / "team_history.json").write_text(json.dumps([
        {"game_id": "old", "completed_at": "2026-10-19T23:00:00Z"}
    ]), encoding="utf-8")
    (dataset / "outcomes.json").write_text(json.dumps([game()]), encoding="utf-8")
    (dataset / "frozen_predictions.json").write_text(json.dumps([
        {"game_id": "espn-1", "market": "moneyline", "selection": "BOS",
         "generated_at": "2026-10-20T20:00:00Z", "model_version": NBA_MODEL_VERSION}
    ]), encoding="utf-8")
    output = tmp_path / "result.json"
    summary = replay_dataset(dataset_dir=dataset, season="2026-27",
                             model_version=NBA_MODEL_VERSION, output=output)
    assert summary["metrics"]["accuracy"] == 1.0
    assert json.loads(output.read_text(encoding="utf-8"))["metrics"]["wins"] == 1


def test_replay_metrics_separate_prediction_quality_from_valid_wagers():
    prediction_time = "2026-10-20T20:00:00Z"
    engine = NBAReplayEngine(season="2026-27", model_version=NBA_MODEL_VERSION,
                             prediction_factory=lambda *_args: [{"market": "moneyline", "selection": "BOS",
                                                                 "model_probability": .70, "generated_at": prediction_time}])
    markets = [{"game_id": "espn-1", "market": "moneyline", "selection": "BOS", "line": None,
                "odds": -125, "provider": "odds", "sportsbook": "book",
                "observed_at": "2026-10-20T19:00:00Z", "validation_status": "VALID_PREGAME"}]
    summary = engine.run(games=[game("SCHEDULED")], history=[], outcomes=[game()], markets=markets)
    metrics = summary["metrics"]
    assert metrics["accuracy"] == 1 and metrics["brierScore"] == pytest.approx(.09)
    assert metrics["logLoss"] == pytest.approx(-__import__('math').log(.7))
    assert metrics["validWagerCount"] == 1 and metrics["units"] == .8 and metrics["roi"] == 80
    assert summary["evaluationPolicy"]["predictionQualitySeparateFromBettingProfitability"] is True


def test_nba_foundation_routes_are_not_public(nba_db, monkeypatch):
    from backend.app import main
    monkeypatch.delenv("NBA_AUTOMATION_SECRET", raising=False)
    client = TestClient(main.app)
    assert client.get("/api/internal/nba/foundation/status").status_code in {401, 403}
    assert client.post("/api/cron/nba-active-season").status_code == 401
