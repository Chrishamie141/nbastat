from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient


def test_nba_history_is_filtered_by_current_user(monkeypatch):
    from backend.app import main
    from backend.app.database import get_db_connection
    from backend.app.services.parlay_history_service import initialize_parlay_history_database
    initialize_parlay_history_database()
    with get_db_connection() as conn:
        for user_id, profile in [(17, "SAFE"), (18, "AGGRESSIVE")]:
            conn.execute("INSERT INTO parlay_history (user_id,sport,created_at,difficulty,legs_json) VALUES (?,?,?,?,?)",
                         (user_id, "NBA", "2030-01-01", profile, "[]"))
    main.app.dependency_overrides[main.require_full_access] = lambda: {"id": 17}
    try:
        response = TestClient(main.app).get("/api/analyze/nba/history")
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    assert response.json()["items"][0]["summary"].startswith("SAFE")


def test_nba_manual_grading_requires_internal_role(monkeypatch):
    from backend.app import main
    from backend.app.services import entitlement_service
    monkeypatch.setattr(entitlement_service, "current_user", lambda request: {"id": 17, "role": "user"})
    monkeypatch.setattr(entitlement_service, "is_internal_user", lambda user: False)
    monkeypatch.setattr(main, "grade_recommendations", lambda *a, **k: pytest.fail("Unauthorized grading ran"))
    response = TestClient(main.app).post("/api/analyze/nba/grade", json={"actualResults": []})
    assert response.status_code == 403


def test_nba_performance_passes_current_identity(monkeypatch):
    from backend.app import main
    monkeypatch.setattr(main, "_metrics", lambda user_id: {"owner": user_id})
    main.app.dependency_overrides[main.require_full_access] = lambda: {"id": 17}
    try:
        response = TestClient(main.app).get("/api/analyze/nba/performance")
    finally:
        main.app.dependency_overrides.clear()
    assert response.json()["metrics"]["owner"] == 17


@pytest.mark.parametrize("extra", [
    {"selections": ["bad"]}, {"selections": [{"gameId": "a"}, {"gameId": "a"}], "mode": "multi_game"},
    {"season": "invalid"}, {"week": None}, {"week": 19}, {"seasonType": "invalid"},
])
def test_analysis_rejects_bad_inputs_before_provider_calls(monkeypatch, extra):
    from backend.app import main
    monkeypatch.setattr(main, "weekly_board", lambda *a, **k: pytest.fail("Invalid input reached provider"))
    main.app.dependency_overrides[main.require_full_access] = lambda: {"id": 17}
    try:
        response = TestClient(main.app).post("/api/nfl/parlays/analysis", json={
            "selections": [{"gameId": "a"}], "season": 2030, "week": 1, **extra,
        })
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 400


@pytest.mark.parametrize("time_mode", ["future", "stale", "started", "naive", "unknown_provider"])
def test_prop_board_omits_unverified_or_non_pregame_quotes(monkeypatch, time_mode):
    import nfl_parlay_builder as builder
    now = datetime.now(timezone.utc)
    stamp = now - timedelta(minutes=1)
    kickoff = now + timedelta(days=1)
    if time_mode == "future": stamp = now + timedelta(hours=1)
    if time_mode == "stale": stamp = now - timedelta(hours=3)
    if time_mode == "started": kickoff = now - timedelta(seconds=1)
    if time_mode == "naive": stamp = stamp.replace(tzinfo=None)
    quote = {"provider": "unknown" if time_mode == "unknown_provider" else "the-odds-api",
             "bookmaker": "Book", "event_id": "event", "last_update": stamp.isoformat(),
             "commence_time": kickoff.isoformat(), "line": 249.5, "odds": -110, "side": "Over"}
    monkeypatch.setattr(builder, "get_nfl_player_props", lambda **k: {"Player": {"PASS_YDS": [quote]}})
    monkeypatch.setattr(builder, "get_nfl_player_recent_stats", lambda **k: {"Player": {"team": "BUF", "PASS_YDS": [260, 270, 280]}})
    assert builder.analyze_nfl_prop_board("balanced", game_teams=("BUF", "MIA"))["rows"] == []


def test_missing_history_does_not_invent_prop_projection(monkeypatch):
    import nfl_parlay_builder as builder
    now = datetime.now(timezone.utc)
    quote = {"provider": "the-odds-api", "bookmaker": "Book", "event_id": "event",
             "last_update": now.isoformat(), "commence_time": (now + timedelta(days=1)).isoformat(),
             "line": 1.5, "odds": -110, "side": "Over"}
    monkeypatch.setattr(builder, "get_nfl_player_props", lambda **k: {"Player": {"PASS_TD": [quote]}})
    monkeypatch.setattr(builder, "get_nfl_player_recent_stats", lambda **k: {"Player": {"team": "BUF"}})
    row = builder.analyze_nfl_prop_board("balanced", game_teams=("BUF", "MIA"))["rows"][0]
    assert row["projection"] is None
    assert row["modelLikelihood"] is None
    assert row["profileEligible"] is False
    assert row["status"] == "INSUFFICIENT_DATA"


def test_error_response_never_exposes_provider_exception():
    from backend.app import main
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as raised:
        main.safe_error("NBA", "analysis", RuntimeError("private-token-and-database-url"))
    assert raised.value.status_code == 503
    assert "private" not in raised.value.detail
