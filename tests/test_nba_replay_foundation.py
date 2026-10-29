from __future__ import annotations

from datetime import date, datetime, timezone
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.app.services.nba.cold_start import build_cold_start_features, build_player_cold_start_features
from backend.app.services.nba.grading import grade_prediction
from backend.app.services.nba.lifecycle import (
    append_market_observation, automation_status, foundation_status, freeze_prediction,
    operational_settings, settle_game, update_operational_settings, upsert_game,
)
from backend.app.services.nba.automation import maybe_reconcile_recent_results, run_cycle
from backend.app.services.nba.game_model import NBA_GAME_MODEL_VERSION
from backend.app.services.nba.production import generate_winner_predictions, performance, prediction_history
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
             "season": {"type": 2, "slug": "regular-season"},
             "status": {"type": {"state": "pre", "detail": "Scheduled"}},
             "competitions": [{"competitors": [
                 {"homeAway": "home", "team": {"abbreviation": "BOS"}},
                 {"homeAway": "away", "team": {"abbreviation": "NYK"}},
             ]}]}
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"events": [event]}
    def get(url, params, headers, timeout):
        assert headers["Accept"] == "application/json"
        assert "SmartBetSports" in headers["User-Agent"]
        assert params["limit"] == 500
        calls.append(params["dates"]); return Response()
    rows = fetch_schedule(season="2026-27", start=date(2026, 9, 20), end=date(2026, 10, 20),
                          http_get=get)
    assert calls == ["202609", "202610"]
    assert len(rows) == 1 and rows[0]["season"] == "2026-27"
    assert rows[0]["season_phase"] == "regular_season"


def test_schedule_retries_failed_month_and_never_silently_drops_it():
    calls = 0
    def fail(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        import requests
        raise requests.Timeout("fixture")
    with pytest.raises(ScheduleIngestionError, match="202610"):
        fetch_schedule(season="2026-27", start=date(2026, 10, 1), end=date(2026, 10, 2),
                       http_get=fail, max_attempts=2, sleeper=lambda _seconds: None)
    assert calls == 4


def test_schedule_recovers_rejected_month_through_daily_cdn():
    import requests

    event = {"id": "1", "date": "2026-10-02T23:00:00Z",
             "season": {"type": 2},
             "status": {"type": {"state": "post", "detail": "Final"}},
             "competitions": [{"competitors": [
                 {"homeAway": "home", "score": "110", "team": {"abbreviation": "BOS"}},
                 {"homeAway": "away", "score": "100", "team": {"abbreviation": "NYK"}},
             ]}]}

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    calls = []

    def get(url, params, **_kwargs):
        calls.append((url, params))
        if "site.api.espn.com" in url:
            raise requests.HTTPError("blocked")
        daily = [event] if params["dates"] == "20261002" else []
        return Response({"content": {"sbData": {"events": daily}}})

    rows = fetch_schedule(
        season="2026-27", start=date(2026, 10, 1), end=date(2026, 10, 2),
        http_get=get, max_attempts=1, sleeper=lambda _seconds: None,
    )

    assert len(rows) == 1
    assert rows[0]["status"] == "FINAL"
    assert [params["dates"] for _url, params in calls] == ["202610", "20261001", "20261002"]


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


def test_production_reconciliation_is_additive_guarded_and_repeatable():
    sql = open(
        "supabase/migrations/20261025083716_production_schema_reconciliation.sql",
        encoding="utf-8",
    ).read().lower()
    assert "existing nba_games rows violate proposed constraints" in sql
    assert "existing nba prediction rows violate proposed constraints" in sql
    assert "add column if not exists season_phase" in sql
    assert "create table if not exists public.nba_operational_settings" in sql
    assert "create table if not exists public.nba_automation_runs" in sql
    assert "nba_prediction_snapshots_immutable_update" in sql
    assert "nba_prediction_snapshots_pregame" in sql
    assert "validate constraint nba_settlements_status_check" in sql
    assert "revoke all privileges on table public.%i from %i" in sql
    assert "on conflict (model_version) do nothing" in sql
    assert "\nupdate public." not in sql
    assert "delete from" not in sql
    assert "truncate " not in sql
    assert "drop table" not in sql


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


def test_nba_cron_accepts_only_configured_bearer_secret(monkeypatch):
    from types import SimpleNamespace
    from backend.app.api import nba_automation_cron as cron
    from backend.app import main

    monkeypatch.setenv("CRON_SECRET", "staging-cron-secret")
    monkeypatch.delenv("NBA_AUTOMATION_SECRET", raising=False)
    monkeypatch.setattr(cron, "operational_settings", lambda: {"enabled": True, "seasonOverride": None})
    monkeypatch.setattr(cron, "active_nba_season", lambda _override=None: SimpleNamespace(code="2026-27"))
    monkeypatch.setattr(cron, "run_cycle", lambda **_kwargs: {
        "status": "SUCCEEDED", "season": "2026-27", "predictionsCreated": 0
    })
    client = TestClient(main.app)

    assert client.get("/api/cron/nba-active-season").status_code == 401
    assert client.get("/api/cron/nba-active-season", headers={"authorization": "Bearer wrong"}).status_code == 401
    response = client.get(
        "/api/cron/nba-active-season", headers={"authorization": "Bearer staging-cron-secret"}
    )
    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert response.json()["season"] == "2026-27"


def test_nba_data_refresh_uses_dedicated_secret_and_trigger(monkeypatch):
    from backend.app.api import nba_automation_cron as cron
    from backend.app import main

    calls = []
    monkeypatch.setenv("NBA_DATA_REFRESH_SECRET", "dedicated-refresh-secret")
    monkeypatch.setattr(
        cron.data_refresh,
        "run",
        lambda **kwargs: calls.append(kwargs) or {"status": "SUCCEEDED"},
    )
    client = TestClient(main.app)

    assert client.post("/api/cron/nba-data-refresh").status_code == 401
    assert client.post(
        "/api/cron/nba-data-refresh",
        headers={"authorization": "Bearer wrong"},
    ).status_code == 401
    response = client.post(
        "/api/cron/nba-data-refresh",
        headers={
            "authorization": "Bearer dedicated-refresh-secret",
            "x-smartbets-trigger": "production_activation",
        },
    )
    assert response.status_code == 200
    assert calls == [{"trigger_source": "production_activation"}]


def test_nba_personnel_cron_is_separately_authenticated_and_disabled_by_default(monkeypatch):
    from types import SimpleNamespace
    from backend.app.api import nba_automation_cron as cron
    from backend.app import main

    monkeypatch.setenv("NBA_PERSONNEL_REFRESH_SECRET", "personnel-only-secret")
    monkeypatch.delenv("NBA_PERSONNEL_REFRESH_ENABLED", raising=False)
    monkeypatch.setattr(cron, "operational_settings", lambda: {"seasonOverride": None})
    monkeypatch.setattr(cron, "active_nba_season", lambda _override=None: SimpleNamespace(code="2026-27"))
    client = TestClient(main.app)

    assert client.post("/api/cron/nba-personnel").status_code == 401
    assert client.post(
        "/api/cron/nba-personnel", headers={"authorization": "Bearer wrong"}
    ).status_code == 401
    response = client.post(
        "/api/cron/nba-personnel",
        headers={"authorization": "Bearer personnel-only-secret"},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "DISABLED", "season": "2026-27"}


def test_nba_personnel_cron_runs_only_personnel_refresh(monkeypatch):
    from types import SimpleNamespace
    from backend.app.api import nba_automation_cron as cron
    from backend.app import main

    monkeypatch.setenv("NBA_PERSONNEL_REFRESH_SECRET", "personnel-only-secret")
    monkeypatch.setenv("NBA_PERSONNEL_REFRESH_ENABLED", "true")
    monkeypatch.setattr(cron, "operational_settings", lambda: {"seasonOverride": "2026-27"})
    monkeypatch.setattr(cron, "active_nba_season", lambda _override=None: SimpleNamespace(code="2026-27"))
    calls = []
    monkeypatch.setattr(cron, "refresh_personnel", lambda **kwargs: calls.append(kwargs) or {
        "status": "FRESH", "season": "2026-27", "teamsAttempted": 30,
        "teamsSucceeded": 30, "playersStored": 518, "errors": [],
    })
    response = TestClient(main.app).post(
        "/api/cron/nba-personnel",
        headers={"authorization": "Bearer personnel-only-secret"},
    )
    assert response.status_code == 200
    assert response.json()["teamsSucceeded"] == 30
    assert calls == [{"season": "2026-27"}]


def test_nba_personnel_cron_skips_overlapping_refresh(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from backend.app.api import nba_automation_cron as cron
    from backend.app import main

    @contextmanager
    def already_running():
        yield False

    monkeypatch.setenv("NBA_PERSONNEL_REFRESH_SECRET", "personnel-only-secret")
    monkeypatch.setenv("NBA_PERSONNEL_REFRESH_ENABLED", "true")
    monkeypatch.setattr(cron, "operational_settings", lambda: {"seasonOverride": None})
    monkeypatch.setattr(cron, "active_nba_season", lambda _override=None: SimpleNamespace(code="2026-27"))
    monkeypatch.setattr(cron, "personnel_refresh_guard", already_running)
    monkeypatch.setattr(
        cron, "refresh_personnel",
        lambda **_kwargs: pytest.fail("overlapping refresh must not execute"),
    )

    response = TestClient(main.app).post(
        "/api/cron/nba-personnel",
        headers={"authorization": "Bearer personnel-only-secret"},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "SKIPPED_OVERLAP", "season": "2026-27"}


def test_postgres_reconciliation_guard_ends_transaction_while_work_runs(monkeypatch):
    from backend.app.services.nba import automation

    events = []

    class Result:
        def fetchone(self):
            return {"acquired": True}

    class Connection:
        def execute(self, query, _values):
            events.append("lock" if "try_advisory" in query else "unlock")
            return Result()

        def commit(self):
            events.append("commit")

        def close(self):
            events.append("close")

    monkeypatch.setattr(automation, "using_postgres", lambda: True)
    monkeypatch.setattr(automation, "get_db_connection", lambda: Connection())

    with automation._result_reconciliation_guard() as acquired:
        assert acquired is True
        events.append("work")

    assert events == ["lock", "commit", "work", "unlock", "commit", "close"]


def test_owner_personnel_refresh_does_not_run_prediction_lifecycle(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from backend.app import main

    @contextmanager
    def acquired():
        yield True

    calls = []
    monkeypatch.setattr(main, "nba_operational_settings", lambda: {"seasonOverride": None})
    monkeypatch.setattr(main, "active_nba_season", lambda _override=None: SimpleNamespace(code="2026-27"))
    monkeypatch.setattr(main, "personnel_refresh_guard", acquired)
    monkeypatch.setattr(main, "refresh_personnel", lambda **kwargs: calls.append(kwargs) or {
        "status": "FRESH", "season": "2026-27", "teamsAttempted": 30,
        "teamsSucceeded": 30, "playersStored": 519, "errors": [],
    })
    monkeypatch.setattr(main, "start_operator_action", lambda *_args: "action-1")
    monkeypatch.setattr(main, "finish_operator_action", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        main,
        "run_nba_cycle",
        lambda **_kwargs: pytest.fail("personnel refresh must not run predictions or settlement"),
    )

    result = main.internal_nba_personnel_refresh(user={"id": 1, "email": "owner@example.com"})

    assert result["status"] == "FRESH"
    assert result["teamsSucceeded"] == 30
    assert calls == [{"season": "2026-27"}]


def test_operational_cycle_generates_once_and_settles_final(nba_db):
    scheduled = game("SCHEDULED", None, None) | {
        "kickoff_time": "2026-10-25T23:00:00Z", "season_phase": "regular_season",
    }
    clock = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
    generator = lambda *, season: generate_winner_predictions(season=season, at=clock)
    personnel = lambda **_kwargs: {"status": "FRESH", "teamsAttempted": 30,
                                   "teamsSucceeded": 30, "playersStored": 450, "errors": []}
    first = run_cycle(season="2026-27", schedule_fetcher=lambda **_kwargs: [scheduled],
                      prediction_generator=generator, personnel_refresher=personnel,
                      trigger_source="test")
    second = run_cycle(season="2026-27", schedule_fetcher=lambda **_kwargs: [scheduled],
                       prediction_generator=generator, personnel_refresher=personnel,
                       trigger_source="test")
    assert first["predictionsCreated"] == 1
    assert second["predictionsCreated"] == 0
    assert second["predictionsAlreadyPresent"] == 1
    rows = prediction_history(season="2026-27")
    assert len(rows) == 1 and rows[0]["modelVersion"] == NBA_GAME_MODEL_VERSION
    assert rows[0]["createdAt"] < rows[0]["kickoffTime"]
    assert rows[0]["modelStatus"] == "RESEARCH_ONLY"
    final = scheduled | {"status": "FINAL", "home_score": 110, "away_score": 100}
    settled = run_cycle(season="2026-27", schedule_fetcher=lambda **_kwargs: [final],
                        prediction_generator=generator, personnel_refresher=personnel,
                        trigger_source="test")
    assert settled["predictionsSettled"] == 1
    assert performance(season="2026-27")["settled"] == 1
    assert automation_status()["latestRun"]["status"] == "SUCCEEDED"


def test_history_reconciliation_self_heals_only_past_unresolved_predictions(nba_db):
    scheduled = game("SCHEDULED", None, None)
    upsert_game(scheduled)
    snapshot_id = freeze_prediction(prediction(model_version=NBA_GAME_MODEL_VERSION))
    final = scheduled | {"status": "FINAL", "home_score": 110, "away_score": 100}
    clock = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)

    first = maybe_reconcile_recent_results(
        season="2026-27",
        schedule_fetcher=lambda **_kwargs: [final],
        at=clock,
        minimum_interval_minutes=15,
    )
    second = maybe_reconcile_recent_results(
        season="2026-27",
        schedule_fetcher=lambda **_kwargs: pytest.fail("settled rows must not refetch"),
        at=clock,
        minimum_interval_minutes=15,
    )

    assert first["status"] == "SUCCEEDED"
    assert first["predictionsSettled"] == 1
    assert second == {"status": "CURRENT", "unresolvedPast": 0}
    row = prediction_history(season="2026-27")[0]
    assert row["id"] == snapshot_id
    assert row["settlementStatus"] == "WIN"
    assert row["actualOutcome"] == "BOS"


def test_runtime_snapshot_triggers_prevent_retroactive_changes(nba_db):
    upsert_game(game("SCHEDULED", None, None))
    snapshot_id = freeze_prediction(prediction())
    from backend.app.database import get_db_connection
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with get_db_connection() as connection:
            connection.execute(
                "UPDATE nba_prediction_snapshots SET selection='NYK' WHERE snapshot_id=?",
                (snapshot_id,),
            )


def test_activation_override_is_persistent_and_validated(nba_db, monkeypatch):
    monkeypatch.delenv("NBA_AUTOMATION_ENABLED", raising=False)
    settings = update_operational_settings(enabled=False, season_override="2027-28", actor="test-admin")
    assert settings == operational_settings()
    assert settings["enabled"] is False
    assert settings["seasonOverride"] == "2027-28"
    assert settings["updatedBy"] == "test-admin"
    with pytest.raises(ValueError):
        update_operational_settings(enabled=True, season_override="bad-season", actor="test-admin")


def test_nba_game_api_reads_exact_game_and_history(nba_db):
    from backend.app import main
    upsert_game(game("SCHEDULED", None, None))
    freeze_prediction(prediction(model_version=NBA_GAME_MODEL_VERSION))
    main.app.dependency_overrides[main.require_full_access] = lambda: {"id": 17}
    try:
        response = TestClient(main.app).get("/api/nba/games/espn-1?season=2026-27")
        history = TestClient(main.app).get("/api/history?tab=NBA")
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["prediction"]["modelVersion"] == NBA_GAME_MODEL_VERSION
    assert history.status_code == 200
    assert history.json()["items"][0]["sport"] == "NBA"


def test_vercel_scheduler_does_not_duplicate_supabase_nba_cycle():
    config = json.loads(open("vercel.json", encoding="utf-8").read())
    jobs = {row["path"]: row["schedule"] for row in config["crons"]}
    assert "/api/cron/nba-active-season" not in jobs
    assert "/api/cron/nba-data-refresh" not in jobs
