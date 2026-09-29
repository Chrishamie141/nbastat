from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from backend.app.database import get_db_connection
from backend.app.services.parlay_ticket_service import create_ticket, get_ticket, grade_tickets, list_tickets


def ticket(ticket_type="SAME_GAME_PARLAY"):
    return {"ticket_type": ticket_type, "sportsbook": "DraftKings", "strategy": "BALANCED",
            "season": 2026, "season_type": "regular", "week": 3, "stake": 5,
            "combined_odds": 300, "potential_payout": 20}


def leg(index=0, game_id="game-1", side="OVER", line=250, player="Sam Test"):
    return {"game_id": game_id, "sportsbook": "DraftKings", "player_id": f"player-{index}",
            "player_name": player, "team": "BAL", "opponent": "PIT", "matchup": "BAL @ PIT",
            "market": "PASS_YDS", "selection": side, "line": line, "odds": -110,
            "smartbet_projection": 265, "smartbet_probability": None,
            "market_implied_probability": .52381, "edge": 15, "confidence": 64,
            "strategy": "BALANCED", "recommendation_state": "SMARTBET_RECOMMENDED",
            "model_version": "nfl_player_prop_matchup_v2", "season": 2026, "week": 3,
            "selected_at": "2026-09-27T12:00:00+00:00", "source_row_id": f"row-{index}"}


def test_confirmed_ticket_preserves_snapshot_and_is_user_scoped():
    created = create_ticket(user_id=7, ticket=ticket(), legs=[leg(0), leg(1, player="Other Test")])
    assert created["status"] == "ACTIVE" and created["number_of_legs"] == 2
    assert created["legs"][0]["model_version"] == "nfl_player_prop_matchup_v2"
    assert created["legs"][0]["recommendation_state"] == "SMARTBET_RECOMMENDED"
    assert json.loads(created["legs"][0]["source_snapshot_json"])["line"] == 250
    assert len(list_tickets(user_id=7)) == 1
    with pytest.raises(KeyError):
        get_ticket(user_id=8, ticket_id=created["ticket_id"])


def test_ticket_validation_rejects_duplicate_conflict_and_cross_game_sgp():
    with pytest.raises(ValueError, match="Duplicate"):
        create_ticket(user_id=7, ticket=ticket(), legs=[leg(0), leg(0)])
    with pytest.raises(ValueError, match="Conflicting"):
        create_ticket(user_id=7, ticket=ticket(), legs=[leg(0), {**leg(1, side="UNDER"), "player_id": "player-0"}])
    with pytest.raises(ValueError, match="one matchup"):
        create_ticket(user_id=7, ticket=ticket(), legs=[leg(0), leg(1, game_id="game-2")])
    created = create_ticket(user_id=7, ticket=ticket("MULTI_GAME_PARLAY"), legs=[leg(0), leg(1, game_id="game-2")])
    assert created["number_of_legs"] == 2


def test_grading_uses_persisted_results_and_leaves_missing_stats_ungraded():
    created = create_ticket(user_id=7, ticket=ticket(), legs=[leg(0), leg(1, line=200, player="Other Test")])
    with get_db_connection() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS nfl_game_result_snapshots (
            game_id TEXT PRIMARY KEY, season INTEGER, season_type TEXT, week INTEGER,
            home_team TEXT, away_team TEXT, home_score INTEGER, away_score INTEGER,
            player_stats_json TEXT)""")
        connection.execute("""INSERT INTO nfl_game_result_snapshots
            (game_id,season,season_type,week,home_team,away_team,home_score,away_score,player_stats_json)
            VALUES (?,?,?,?,?,?,?,?,?)""", ("game-1", 2026, "regular", 3, "PIT", "BAL", 20, 24,
                                             json.dumps({"Sam Test": {"PASS_YDS": 250}})))
    result = grade_tickets(season=2026, season_type="regular", week=3)
    assert result == {"graded": 0, "pending": 1}
    current = get_ticket(user_id=7, ticket_id=created["ticket_id"])
    assert current["status"] == "PARTIAL"
    assert [row["leg_status"] for row in current["legs"]] == ["PUSH", "UNGRADED"]


def test_grading_applies_parlay_void_semantics_and_official_stat_corrections():
    void_leg = {**leg(1, player="Voided Test"), "void": True}
    created = create_ticket(user_id=7, ticket=ticket(), legs=[leg(0), void_leg])
    with get_db_connection() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS nfl_game_result_snapshots (
            game_id TEXT PRIMARY KEY, season INTEGER, season_type TEXT, week INTEGER,
            home_team TEXT, away_team TEXT, home_score INTEGER, away_score INTEGER,
            player_stats_json TEXT)""")
        connection.execute("""INSERT INTO nfl_game_result_snapshots
            (game_id,season,season_type,week,home_team,away_team,home_score,away_score,player_stats_json)
            VALUES (?,?,?,?,?,?,?,?,?)""", ("game-1", 2026, "regular", 3, "PIT", "BAL", 20, 24,
                                             json.dumps({"Sam Test": {"PASS_YDS": 260}})))
    assert grade_tickets(season=2026, season_type="regular", week=3) == {"graded": 1, "pending": 0}
    current = get_ticket(user_id=7, ticket_id=created["ticket_id"])
    assert current["status"] == "WON"
    assert [row["leg_status"] for row in current["legs"]] == ["WON", "VOID"]
    assert current["legs"][0]["final_result"] == 260

    # A provider correction must recompute even an already-settled ticket.
    with get_db_connection() as connection:
        connection.execute("UPDATE nfl_game_result_snapshots SET player_stats_json=? WHERE game_id=?",
                           (json.dumps({"Sam Test": {"PASS_YDS": 240}}), "game-1"))
    grade_tickets(season=2026, season_type="regular", week=3)
    corrected = get_ticket(user_id=7, ticket_id=created["ticket_id"])
    assert corrected["status"] == "LOST"
    assert corrected["legs"][0]["leg_status"] == "LOST"
    assert corrected["legs"][0]["final_result"] == 240


def test_authenticated_ticket_api_rebuilds_authoritative_rows_and_enforces_ownership(monkeypatch):
    from backend.app import main

    game = {
        "game_id": "future-game", "status": "scheduled",
        "kickoff_time": "2030-09-29T17:00:00+00:00",
        "away_team": "BAL", "home_team": "PIT",
    }
    rows = [
        {"rowId": "pass-over", "playerId": "qb-1", "player": "Sam Test", "team": "BAL",
         "market": "PASS_YDS", "side": "OVER", "line": 250.5, "odds": -115,
         "projection": 260.0, "modelSide": "OVER", "modelLikelihood": 64.0,
         "profileEligible": True, "bookmaker": "DraftKings", "status": "READY",
         "marketTimestamp": "2030-09-29T12:00:00+00:00"},
        {"rowId": "rush-over", "playerId": "rb-1", "player": "Other Test", "team": "PIT",
         "market": "RUSH_YDS", "side": "OVER", "line": 70.5, "odds": -110,
         "projection": 78.0, "modelSide": "UNDER", "modelLikelihood": 55.0,
         "profileEligible": False, "bookmaker": "DraftKings", "status": "READY",
         "marketTimestamp": "2030-09-29T12:00:00+00:00"},
    ]
    monkeypatch.setattr(main, "weekly_board", lambda *args, **kwargs: {"items": [game]})
    monkeypatch.setattr(main, "analyze_nfl_prop_board", lambda profile, game_teams, **kwargs: {"rows": rows})
    main.app.dependency_overrides[main.require_full_access] = lambda: {"id": 17}
    try:
        client = TestClient(main.app)
        response = client.post("/api/nfl/parlay-tickets", json={
            "ticketType": "SAME_GAME_PARLAY", "sportsbook": "DraftKings",
            "strategy": "BALANCED", "season": 2030, "seasonType": "regular", "week": 4,
            "legs": [
                {"gameId": "future-game", "rowId": "pass-over", "selectedAt": "2026-09-27T12:30:00+00:00",
                 "projection": 9999, "odds": 9999},
                {"gameId": "future-game", "rowId": "rush-over", "selectedAt": "2026-09-27T12:31:00+00:00"},
            ],
        })
        assert response.status_code == 200
        saved = response.json()
        assert saved["user_id"] == 17
        assert saved["legs"][0]["smartbet_projection"] == 260.0
        assert saved["legs"][0]["odds"] == -115
        assert saved["legs"][0]["recommendation_state"] == "SMARTBET_RECOMMENDED"
        assert saved["legs"][1]["recommendation_state"] == "MANUAL_SELECTION"
        assert client.get("/api/nfl/parlay-tickets").json()["items"][0]["ticket_id"] == saved["ticket_id"]

        main.app.dependency_overrides[main.require_full_access] = lambda: {"id": 18}
        assert client.get(f"/api/nfl/parlay-tickets/{saved['ticket_id']}").status_code == 404
    finally:
        main.app.dependency_overrides.clear()


def test_ticket_api_rejects_selection_timestamp_at_or_after_kickoff(monkeypatch):
    from backend.app import main

    game = {"game_id": "future-game", "status": "scheduled", "kickoff_time": "2030-09-29T17:00:00+00:00",
            "away_team": "BAL", "home_team": "PIT"}
    rows = [{"rowId": name, "playerId": name, "player": name, "team": "BAL", "market": "PASS_YDS",
             "side": "OVER", "line": 200.5 + index, "odds": -110, "projection": 220,
             "modelSide": "OVER", "modelLikelihood": 60, "profileEligible": True,
             "bookmaker": "DraftKings"} for index, name in enumerate(("one", "two"))]
    monkeypatch.setattr(main, "weekly_board", lambda *args, **kwargs: {"items": [game]})
    monkeypatch.setattr(main, "analyze_nfl_prop_board", lambda profile, game_teams, **kwargs: {"rows": rows})
    main.app.dependency_overrides[main.require_full_access] = lambda: {"id": 17}
    try:
        response = TestClient(main.app).post("/api/nfl/parlay-tickets", json={
            "ticketType": "SAME_GAME_PARLAY", "sportsbook": "DraftKings", "strategy": "BALANCED",
            "season": 2030, "seasonType": "regular", "week": 4,
            "legs": [{"gameId": "future-game", "rowId": row["rowId"],
                      "selectedAt": "2030-09-29T17:00:00+00:00"} for row in rows],
        })
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 400
