from __future__ import annotations

import json
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
    assert 0 <= safe["items"][0]["homeWinProbability"] <= 1
    assert 0 <= safe["items"][0]["awayWinProbability"] <= 1
    assert safe["items"][0]["homeWinProbability"] + safe["items"][0]["awayWinProbability"] == 1
    assert int(aggressive["items"][0]["recommended"]) >= int(safe["items"][0]["recommended"])


def test_schedule_uses_espn_cdn_when_scoreboard_is_blocked(monkeypatch):
    import backend.app.services.nfl_product_service as service

    event = {
        "id": "401-test",
        "date": "2026-09-13T17:00Z",
        "competitions": [{
            "venue": {"fullName": "Test Stadium"},
            "broadcasts": [{"names": ["CBS"]}],
            "competitors": [
                {"homeAway": "home", "score": "0", "team": {"abbreviation": "BUF", "displayName": "Buffalo Bills"}},
                {"homeAway": "away", "score": "0", "team": {"abbreviation": "MIA", "displayName": "Miami Dolphins"}},
            ],
        }],
        "status": {"type": {"state": "pre", "completed": False}},
    }
    payload = {"content": {"schedule": {"20260913": {"games": [event]}}}}
    requested = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    def fake_urlopen(request, timeout):
        requested.append(request.full_url)
        if request.full_url.startswith(service.ESPN_SCOREBOARD):
            raise OSError("scoreboard blocked")
        return Response()

    service._schedule.cache_clear()
    monkeypatch.setattr(service, "urlopen", fake_urlopen)
    games = service._schedule(2026, 1)

    assert len(requested) == 2
    assert requested[1].startswith(service.ESPN_SCHEDULE_CDN)
    assert games[0]["home_team"] == "BUF"
    assert games[0]["away_team"] == "MIA"
    assert games[0]["status"] == "scheduled"


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
