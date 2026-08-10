from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _game(status="scheduled"):
    return {
        "game_id": "espn-test", "season": 2026, "week": 1,
        "kickoff_time": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        "home_team": "BUF", "away_team": "MIA", "home_name": "Buffalo Bills",
        "away_name": "Miami Dolphins", "home_score": 0, "away_score": 0,
        "venue": "Test Stadium", "broadcast": [], "status": status,
    }


def test_weekly_profiles_keep_every_game_and_only_change_recommendation(monkeypatch):
    import backend.app.services.nfl_product_service as service

    monkeypatch.setattr(service, "_schedule", lambda season, week: [_game()])
    monkeypatch.setattr(service, "_odds_by_game", lambda bucket: {})
    monkeypatch.setattr(service, "_save_prediction", lambda *args: None)

    safe = service.weekly_board(2026, 1, "SAFE", 4)
    aggressive = service.weekly_board(2026, 1, "AGGRESSIVE", 4)

    assert safe["gameCount"] == aggressive["gameCount"] == 1
    assert safe["items"][0]["predictionStatus"] == "available"
    assert safe["items"][0]["winner"] == aggressive["items"][0]["winner"]
    assert safe["items"][0]["winProbability"] == aggressive["items"][0]["winProbability"]
    assert int(aggressive["items"][0]["recommended"]) >= int(safe["items"][0]["recommended"])


def test_historical_games_never_reconstruct_missing_pregame_prediction(monkeypatch, tmp_path):
    import backend.app.services.nfl_product_service as service

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'history.db').as_posix()}")
    final = _game("final")
    final.update(home_score=27, away_score=20)
    monkeypatch.setattr(service, "_schedule", lambda season, week: [final] if week == 1 else [])

    rows = service.historical_games(2026, 8)

    assert len(rows) == 1
    assert rows[0]["actualWinner"] == "BUF"
    assert rows[0]["prediction"] is None
    assert rows[0]["predictionResult"] is None


def test_depth_charts_are_user_scoped_and_support_crud(monkeypatch, tmp_path):
    import backend.app.services.nfl_product_service as service

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'depth.db').as_posix()}")
    created = service.save_depth_chart(11, "Sunday starters", {"QB": "player-1", "NOT_A_SLOT": "bad"})

    assert created["formation"] == {"QB": "player-1"}
    assert service.list_depth_charts(12) == []

    updated = service.save_depth_chart(11, "Updated", {"RB": "player-2"}, created["id"])
    assert updated["name"] == "Updated"
    assert updated["formation"] == {"RB": "player-2"}

    service.delete_depth_chart(11, created["id"])
    assert service.list_depth_charts(11) == []
