from datetime import datetime, timezone
import json
import sqlite3


def seed_week1(path, at):
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE experiment(id TEXT,manifest TEXT,manifest_hash TEXT,credits_limit INTEGER,reserved INTEGER,remaining INTEGER,quota_state TEXT,next_request REAL,last_status TEXT);
        CREATE TABLE games(game_id TEXT,home_team TEXT,away_team TEXT,kickoff TEXT,kickoff_epoch REAL);
        CREATE TABLE forward_predictions(game_id TEXT,experiment_id TEXT,model_version TEXT,model_hash TEXT,winner TEXT,probability REAL,generated_at TEXT,generated_epoch REAL,input_json TEXT,prediction_json TEXT,prediction_hash TEXT);
        CREATE TABLE forward_finals(game_id TEXT,home_score INTEGER,away_score INTEGER,source TEXT,retrieved_at TEXT);
        CREATE TABLE forward_grades(game_id TEXT,result TEXT,graded_at TEXT);
        CREATE TABLE checkpoints(game_id TEXT,offset_minutes INTEGER,due REAL,deadline REAL,state TEXT);
        CREATE TABLE captures(game_id TEXT,offset_minutes INTEGER,request_id TEXT,retrieved_at TEXT,stored_at TEXT,kickoff TEXT,cutoff_epoch REAL,event_id TEXT,bookmaker TEXT,market_type TEXT,source_time TEXT,source_epoch REAL,retrieved_epoch REAL,outcomes TEXT,provenance TEXT);
        CREATE TABLE requests(id TEXT,requested_at TEXT,completed_at TEXT,state TEXT,credits_reserved INTEGER,remaining INTEGER,used INTEGER,last_cost INTEGER,response_hash TEXT,http_status INTEGER);
        CREATE TABLE forward_events(event_id TEXT,at TEXT,kind TEXT,detail TEXT);
    """)
    manifest = {"season": 2026, "phase": "regular", "week": 1, "frozen_model_reference": "nfl_game_baseline_v3", "model_tuning": False}
    connection.execute("INSERT INTO experiment VALUES(?,?,?,?,?,?,?,?,?)", ("NFL-2026-REG1-v1", json.dumps(manifest), "manifest", 90, 3, 99997, "KNOWN", None, "READY"))
    for index in range(16):
        game_id = f"espn-{index:03d}"
        kickoff = "2030-09-10T00:20:00+00:00"
        connection.execute("INSERT INTO games VALUES(?,?,?,?,?)", (game_id, f"H{index}", f"A{index}", kickoff, 1915230000.0))
        connection.execute("INSERT INTO forward_predictions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           (game_id, "NFL-2026-REG1-v1", "nfl_game_baseline_v3", "model-hash", f"H{index}", .6,
                            at.isoformat(), at.timestamp(), "{}", "{}", f"hash-{index}"))
        for offset in (1440, 360, 60):
            connection.execute("INSERT INTO checkpoints VALUES(?,?,?,?,?)", (game_id, offset, 1915000000.0, 1915001000.0, "PENDING"))
    connection.commit()
    connection.close()


def seed_social(tmp_path, monkeypatch, at):
    from backend.app.services import social_marketing as social
    monkeypatch.setenv("SOCIAL_DATABASE_URL", "sqlite:///" + (tmp_path / "operations.db").as_posix())
    monkeypatch.setenv("SOCIAL_SOURCE_SIGNING_KEY", "test-signing-key-not-production-123456")
    monkeypatch.setenv("SOCIAL_CAMPAIGN_START", "2030-09-01")
    monkeypatch.setenv("SOCIAL_AUTO_PUBLISH", "false")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.delenv("X_EXPECTED_USER_ID", raising=False)
    social.initialize()
    source = {"verified_at": at.isoformat(), "preseason": {"record": {"WIN": 11, "LOSS": 4, "PUSH": 1}, "predictions": 16, "qualified_wagers": 0},
              "regular": {"season": 2026, "phase": "regular", "week": 1, "predictions": 16, "scheduled": 16, "graded": 0,
                          "winner_record": {"WIN": 0, "LOSS": 0, "PUSH": 0}}, "coverage_games": 0,
              "claims_policy": "predictions_are_not_wagers;small_sample_not_future_performance"}
    social.store_source(source)


def test_social_post_history_is_paginated_filterable_and_secret_safe(tmp_path, monkeypatch):
    from backend.app.services import social_marketing as social
    from backend.app.services.operations_dashboard_service import social_post_history
    at = datetime(2030, 9, 1, 13, 30, tzinfo=timezone.utc)
    seed_social(tmp_path, monkeypatch, at)
    with social.connection() as connection:
        source_id = connection.execute("SELECT source_id FROM social_sources LIMIT 1").fetchone()["source_id"]
        for index, status in enumerate(("PUBLISHED", "DRAFT", "FAILED")):
            connection.execute(
                "INSERT INTO social_posts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"post-{index}", "campaign", f"evidence|{index}", f"Post content {index}", source_id,
                 at.isoformat(), at.isoformat(), at.isoformat() if status == "PUBLISHED" else None,
                 "2095585810376483322" if status == "PUBLISHED" else None, status,
                 "TEST_FAILURE" if status == "FAILED" else None, f"content-{index}", f"secret-{index}", f"2030-09-0{index + 1}"),
            )
    first = social_post_history(limit=2)
    published = social_post_history(status="published")
    assert first["total"] == 3 and len(first["items"]) == 2 and first["hasMore"] is True
    assert published["total"] == 1 and published["items"][0]["status"] == "PUBLISHED"
    assert published["items"][0]["xUrl"].endswith("2095585810376483322")
    assert "signature" not in published["items"][0] and "content_hash" not in published["items"][0]


def test_command_center_is_week1_operational_control_plane(tmp_path, monkeypatch):
    from backend.app.services.operations_dashboard_service import command_center
    at = datetime(2030, 9, 1, 13, 30, tzinfo=timezone.utc)
    week_db = tmp_path / "week1.db"
    seed_week1(week_db, at)
    seed_social(tmp_path, monkeypatch, at)
    monkeypatch.setenv("WEEK1_EXPERIMENT_DB", str(week_db))

    result = command_center(lambda: at)

    assert result["week1Readiness"]["status"] == "READY"
    assert result["summary"]["gamesReady"] == 16
    assert result["summary"]["predictionsReady"] == 16
    assert len(result["games"]) == 16
    assert result["modelOperations"]["productionModel"] == "nfl_game_baseline_v3"
    assert result["automation"]["publishingBlocked"] is True
    assert result["automation"]["dryRun"] is True
    assert result["automation"]["autoPublish"] is False
    assert not any(issue["category"] == "DATABASE" for issue in result["issues"])
    assert "buyers" not in result


def test_command_center_partial_week_store_failure_is_explicit(tmp_path, monkeypatch):
    from backend.app.services.operations_dashboard_service import command_center
    at = datetime(2030, 9, 1, 13, 30, tzinfo=timezone.utc)
    seed_social(tmp_path, monkeypatch, at)
    monkeypatch.setenv("WEEK1_EXPERIMENT_DB", str(tmp_path / "missing.db"))
    result = command_center(lambda: at)
    assert result["panels"]["week1"]["status"] == "UNAVAILABLE"
    assert result["summary"]["gamesTotal"] == 16
    assert result["week1Readiness"]["status"] == "DEGRADED"
    assert any(issue["category"] == "DATA" for issue in result["issues"])


def test_operator_action_history_records_lifecycle():
    from backend.app.services.operator_action_service import finish, recent, start
    action_id = start("REFRESH_GAME", "operator@example.com", "espn-1")
    finish(action_id, result="SUCCEEDED", status_change="scheduled->pregame")
    row = recent(1)[0]
    assert row["action_id"] == action_id
    assert row["result"] == "SUCCEEDED"
    assert row["completed_at"]


def test_critical_database_failure_marks_week1_not_ready(tmp_path, monkeypatch):
    from backend.app.services import operations_dashboard_service as operations
    at = datetime(2030, 9, 1, 13, 30, tzinfo=timezone.utc)
    week_db = tmp_path / "week1.db"
    seed_week1(week_db, at)
    seed_social(tmp_path, monkeypatch, at)
    monkeypatch.setenv("WEEK1_EXPERIMENT_DB", str(week_db))
    monkeypatch.setattr(operations, "database_health", lambda: {"status": "unhealthy", "errorCode": "DATABASE_UNAVAILABLE"})
    result = operations.command_center(lambda: at)
    assert result["week1Readiness"]["status"] == "NOT_READY"
    assert any(issue["severity"] == "CRITICAL" and issue["category"] == "DATABASE" for issue in result["issues"])


def test_command_center_routes_require_internal_access():
    from backend.app.main import app
    paths = app.openapi()["paths"]
    for route, method in (("/api/internal/operations", "get"),
                          ("/api/internal/operations/health", "get"),
                          ("/api/internal/operations/social-posts", "get"),
                          ("/api/internal/operations/search", "get"),
                          ("/api/internal/operations/refresh", "post"),
                          ("/api/internal/operations/games/{game_id}", "get"),
                          ("/api/internal/operations/games/{game_id}/refresh", "post"),
                          ("/api/internal/operations/games/{game_id}/reconcile", "post")):
        assert paths[route][method]["responses"]["200"]


def test_owner_refresh_is_free_and_audited(monkeypatch):
    from backend.app import main
    events = []
    monkeypatch.setattr(main, "start_operator_action", lambda *args: events.append(("start", args)) or "action-1")
    monkeypatch.setattr(main, "finish_operator_action", lambda *args, **kwargs: events.append(("finish", args, kwargs)))
    monkeypatch.setattr(main, "refresh_games", lambda *args, **kwargs: ([object()] * 16, False))
    result = main.api_internal_refresh_all(user={"id": 1, "email": "owner@example.com"})
    assert result["status"] == "SUCCEEDED"
    assert result["gameCount"] == 16
    assert result["paidProviderContacted"] is False
    assert [event[0] for event in events] == ["start", "finish"]


def test_outcome_reconciliation_fails_closed_before_kickoff(monkeypatch):
    import pytest
    from fastapi import HTTPException
    from backend.app import main
    monkeypatch.setattr(main, "command_center", lambda: {"games": [{"id": "espn-1", "kickoff": "2099-01-01T00:00:00+00:00"}]})
    with pytest.raises(HTTPException, match="blocked before kickoff") as error:
        main.api_internal_reconcile_game("espn-1", user={"id": 1, "email": "owner@example.com"})
    assert error.value.status_code == 409


def test_owner_search_preserves_catalog_result_types(monkeypatch):
    from backend.app import main
    monkeypatch.setattr(main, "command_center", lambda: {"games": [{"id": "espn-1", "awayTeam": "NE", "homeTeam": "SEA", "status": "SCHEDULED"}],
                                                         "issues": [], "modelOperations": {"productionModel": "baseline"}})
    monkeypatch.setattr(main, "search_catalog", lambda query, games: {"items": [
        {"type": "team", "id": "sea", "name": "Seattle Seahawks"},
        {"type": "player", "id": "p1", "name": "Example Player"},
    ]})
    result = main.api_internal_operations_search("SEA", user={"id": 1})
    assert len(result["games"]) == 1
    assert result["teams"][0]["name"] == "Seattle Seahawks"
    assert result["players"][0]["name"] == "Example Player"
