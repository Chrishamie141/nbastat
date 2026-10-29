from datetime import datetime, timezone

import pytest

from backend.app.database import get_db_connection
from backend.app.services import nfl_current_season_pipeline as pipeline
from backend.app.services import nfl_product_service as product


NOW = datetime(2026, 10, 28, 16, 0, tzinfo=timezone.utc)


def _game(game_id, week, *, status, kickoff, home="KC", away="BUF", home_score=None, away_score=None):
    return {
        "game_id": game_id, "season": 2026, "week": week, "provider_week": week,
        "season_type": "regular", "kickoff_time": kickoff, "home_team": home,
        "away_team": away, "home_score": home_score, "away_score": away_score,
        "status": status,
    }


def _schedules():
    return {
        1: [_game("espn-final-1", 1, status="final", kickoff="2026-09-10T00:20:00Z",
                  home_score=24, away_score=21)],
        2: [_game("espn-upcoming-2", 2, status="scheduled", kickoff="2026-11-01T18:00:00Z")],
    }


def _loader_factory(schedules, failures=0):
    calls = {"failures": 0}

    def load(_season, week, _season_type):
        if calls["failures"] < failures:
            calls["failures"] += 1
            raise OSError("temporary provider outage")
        return schedules.get(week, [])

    return load, calls


def _context(_season, at=None):
    return {"season": 2026, "seasonType": "regular", "week": 2,
            "displayWeek": 2, "providerWeek": 2, "source": "test"}


def test_cloud_cycle_ingests_freezes_versions_and_is_idempotent(monkeypatch):
    monkeypatch.setenv("NFL_DATA_REFRESH_ENABLED", "true")
    schedules = _schedules()
    loader, _ = _loader_factory(schedules)

    before_static = len(product._history())
    first = pipeline.run(season=2026, schedule_loader=loader, context_resolver=_context,
                         clock=lambda: NOW, trigger_source="test")
    assert first["status"] == "SUCCEEDED"
    assert first["gamesObserved"] == 1
    assert first["historyRowsInserted"] == 2
    assert first["forecastsCreated"] == 1
    assert first["officialsFrozen"] == 1
    assert first["latestHistoryCutoff"] == "2026-09-10T06:20:00+00:00"
    assert len(product._history()) == before_static + 2

    with get_db_connection() as connection:
        history = connection.execute("SELECT * FROM nfl_team_game_history ORDER BY team").fetchall()
        official = connection.execute("SELECT * FROM nfl_game_predictions WHERE user_id=0").fetchone()
        forecast = connection.execute("SELECT * FROM nfl_forecast_snapshots").fetchone()
        nba_before = connection.execute(
            "SELECT COUNT(*) AS count FROM sqlite_master WHERE type='table' AND name LIKE 'nba_%'"
        ).fetchone()["count"]
    assert len(history) == 2
    assert {row["team"] for row in history} == {"BUF", "KC"}
    assert all(row["source_completed_at"] is None for row in history)
    assert official is not None and forecast is not None
    frozen_json = official["prediction_json"]

    second = pipeline.run(season=2026, schedule_loader=loader, context_resolver=_context,
                          clock=lambda: NOW, trigger_source="test_retry")
    assert second["historyRowsInserted"] == 0
    assert second["duplicateRows"] == 2
    assert second["forecastsCreated"] == 0
    assert second["forecastsUnchanged"] == 1
    assert second["officialsFrozen"] == 0
    with get_db_connection() as connection:
        assert connection.execute("SELECT COUNT(*) AS count FROM nfl_team_game_history").fetchone()["count"] == 2
        assert connection.execute("SELECT COUNT(*) AS count FROM nfl_forecast_snapshots").fetchone()["count"] == 1
        assert connection.execute("SELECT prediction_json FROM nfl_game_predictions WHERE user_id=0").fetchone()["prediction_json"] == frozen_json
        nba_after = connection.execute(
            "SELECT COUNT(*) AS count FROM sqlite_master WHERE type='table' AND name LIKE 'nba_%'"
        ).fetchone()["count"]
    assert nba_after == nba_before


def test_new_final_advances_cutoff_and_changes_forecast_input(monkeypatch):
    monkeypatch.setenv("NFL_DATA_REFRESH_ENABLED", "true")
    schedules = _schedules()
    loader, _ = _loader_factory(schedules)
    pipeline.run(season=2026, schedule_loader=loader, context_resolver=_context,
                 clock=lambda: NOW, trigger_source="test")
    with get_db_connection() as connection:
        first = connection.execute("SELECT history_hash,history_row_count FROM nfl_forecast_snapshots").fetchone()

    schedules[2] = [
        _game("espn-final-2", 2, status="final", kickoff="2026-10-20T00:20:00Z",
              home="DAL", away="NYG", home_score=10, away_score=27),
        _game("espn-upcoming-2b", 2, status="scheduled", kickoff="2026-11-02T18:00:00Z",
              home="DAL", away="NYG"),
    ]
    second = pipeline.run(season=2026, schedule_loader=loader, context_resolver=_context,
                          clock=lambda: NOW, trigger_source="test")
    assert second["historyRowsInserted"] == 2
    assert second["latestHistoryCutoff"] == "2026-10-20T06:20:00+00:00"
    with get_db_connection() as connection:
        latest = connection.execute("""SELECT history_hash,history_row_count FROM nfl_forecast_snapshots
            WHERE game_id='espn-upcoming-2b'""").fetchone()
    assert latest["history_hash"] != first["history_hash"]
    assert latest["history_row_count"] == first["history_row_count"] + 2


def test_started_games_are_not_forecast_and_missing_final_data_fails_closed(monkeypatch):
    monkeypatch.setenv("NFL_DATA_REFRESH_ENABLED", "true")
    schedules = _schedules()
    schedules[2] = [_game("started", 2, status="scheduled", kickoff="2026-10-28T15:00:00Z")]
    loader, _ = _loader_factory(schedules)
    result = pipeline.run(season=2026, schedule_loader=loader, context_resolver=_context,
                          clock=lambda: NOW, trigger_source="test")
    assert result["forecastsCreated"] == 0
    with get_db_connection() as connection:
        assert connection.execute("SELECT COUNT(*) AS count FROM nfl_forecast_snapshots").fetchone()["count"] == 0

    bad = _schedules()
    bad[1][0]["home_score"] = None
    bad_loader, _ = _loader_factory(bad)
    with pytest.raises(ValueError, match="FINAL_GAME_INCOMPLETE"):
        pipeline.run(season=2026, schedule_loader=bad_loader, context_resolver=_context,
                     clock=lambda: NOW, trigger_source="test_bad")
    with get_db_connection() as connection:
        state = connection.execute("SELECT * FROM nfl_data_pipeline_state WHERE singleton=1").fetchone()
    assert state["last_status"] == "FAILED"
    assert state["consecutive_failures"] == 1


def test_provider_retry_recovers_without_duplicate_write(monkeypatch):
    monkeypatch.setenv("NFL_DATA_REFRESH_ENABLED", "true")
    loader, calls = _loader_factory(_schedules(), failures=2)
    result = pipeline.run(season=2026, schedule_loader=loader, context_resolver=_context,
                          clock=lambda: NOW, trigger_source="test")
    assert result["status"] == "SUCCEEDED"
    assert calls["failures"] == 2
    assert result["historyRowsInserted"] == 2


def test_disabled_cycle_makes_no_provider_contact(monkeypatch):
    monkeypatch.setenv("NFL_DATA_REFRESH_ENABLED", "false")
    contacted = False

    def loader(*_args):
        nonlocal contacted
        contacted = True
        return []

    result = pipeline.run(season=2026, schedule_loader=loader, context_resolver=_context,
                          clock=lambda: NOW)
    assert result["status"] == "DISABLED"
    assert result["networkContacted"] is False
    assert contacted is False


def test_disabled_cycle_does_not_initialize_database(monkeypatch):
    monkeypatch.setenv("NFL_DATA_REFRESH_ENABLED", "false")
    monkeypatch.setattr(
        pipeline, "initialize",
        lambda: (_ for _ in ()).throw(AssertionError("database must remain untouched")),
    )

    result = pipeline.run(season=2026)

    assert result["status"] == "DISABLED"
    assert result["networkContacted"] is False


@pytest.mark.parametrize(
    ("vercel_environment", "expected"),
    [("production", "production"), ("preview", "staging"), ("development", "staging")],
)
def test_deployment_environment_defaults_from_vercel(monkeypatch, vercel_environment, expected):
    monkeypatch.delenv("NFL_DATA_REFRESH_ENVIRONMENT", raising=False)
    monkeypatch.setenv("VERCEL_ENV", vercel_environment)

    assert pipeline._deployment_environment() == expected
    assert pipeline._job_name() == f"smartbets-nfl-data-refresh-30m-{expected}"


def test_deployment_environment_rejects_unreviewed_values(monkeypatch):
    monkeypatch.setenv("NFL_DATA_REFRESH_ENVIRONMENT", "prod-ish")

    with pytest.raises(RuntimeError, match="NFL_DATA_REFRESH_ENVIRONMENT_INVALID"):
        pipeline._deployment_environment()


def test_pipeline_database_url_prefers_dedicated_credential(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://broad.example/app")
    monkeypatch.setenv(
        "NFL_DATA_REFRESH_DATABASE_URL",
        "postgresql://smartbets_production_app@scoped.example/postgres",
    )

    assert pipeline._pipeline_database_url() == (
        "postgresql://smartbets_production_app@scoped.example/postgres"
    )
    assert pipeline._pipeline_using_postgres() is True


def test_postgres_lock_is_session_scoped_and_explicitly_released(monkeypatch):
    class Cursor:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Connection:
        def __init__(self):
            self.queries = []
            self.commits = 0
            self.closed = False

        def execute(self, query):
            self.queries.append(query)
            if "pg_try_advisory_lock" in query:
                return Cursor({"locked": True})
            return Cursor({"unlocked": True})

        def commit(self):
            self.commits += 1

        def close(self):
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(pipeline, "_pipeline_using_postgres", lambda: True)
    monkeypatch.setattr(pipeline, "_pipeline_connection", lambda: connection)

    acquired_connection, acquired = pipeline._acquire_pipeline_lock()
    assert acquired is True
    assert acquired_connection is connection
    assert connection.closed is False
    assert "pg_try_advisory_lock" in connection.queries[0]

    pipeline._release_pipeline_lock(acquired_connection)
    assert any("pg_advisory_unlock" in query for query in connection.queries)
    assert connection.commits == 2
    assert connection.closed is True
