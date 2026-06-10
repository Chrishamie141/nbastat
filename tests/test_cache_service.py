from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cache_service
import roster_service


def test_roster_cache_falls_back_when_live_lookup_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cache_service, "CACHE_DIR", tmp_path)
    cache_service.save_roster_cache("SAS", ["Victor Wembanyama", "De'Aaron Fox"])

    def fail_live_lookup(*args, **kwargs):
        raise ValueError("simulated timeout")

    monkeypatch.setattr(roster_service, "get_team_roster", fail_live_lookup)
    team_id, roster, status = roster_service.get_roster_with_cache("SAS")

    assert team_id is not None
    assert roster == ["Victor Wembanyama", "De'Aaron Fox"]
    assert status == "CACHE"
    assert "Using cached roster for SAS" in capsys.readouterr().out


def test_stale_roster_cache_is_used_when_live_lookup_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cache_service, "CACHE_DIR", tmp_path)
    stale_time = (datetime.now(timezone.utc) - timedelta(hours=80)).isoformat()
    (tmp_path / "roster_SAS.json").write_text(
        json.dumps({"team_abbr": "SAS", "cached_at": stale_time, "roster": ["Victor Wembanyama"]}),
        encoding="utf-8",
    )

    def fail_live_lookup(*args, **kwargs):
        raise ValueError("simulated timeout")

    monkeypatch.setattr(roster_service, "get_team_roster", fail_live_lookup)
    _, roster, status = roster_service.get_roster_with_cache("SAS")

    assert roster == ["Victor Wembanyama"]
    assert status == "STALE CACHE"
    assert "older than 72 hours; using stale cache" in capsys.readouterr().out


def test_prediction_cache_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_service, "CACHE_DIR", tmp_path)
    rows = [{"player": "Jalen Brunson", "stat_type": "PTS", "projection": 28.4}]

    cache_service.save_prediction_cache("2026-06-08", "NYK", "SAS", rows)
    cached = cache_service.load_prediction_cache("2026-06-08", "NYK", "SAS")

    assert cached["team"] == "NYK"
    assert cached["opponent"] == "SAS"
    assert cached["prediction_rows"] == rows


def test_collect_betting_predictions_uses_prediction_cache_when_opponent_roster_missing(monkeypatch):
    import app

    answers = iter(["NYK", "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    monkeypatch.setattr(
        app,
        "get_next_game_context",
        lambda team, season="2025-26": {
            "opponent": "SAS",
            "home": True,
            "playoff_game": True,
            "game_date": "2026-06-08",
            "game_id": "1",
            "source": "test",
        },
    )

    def roster_with_cache(team, season="2025-26"):
        if team == "NYK":
            return 1, ["Jalen Brunson"], "LIVE"
        return None, [], "UNAVAILABLE"

    monkeypatch.setattr(app, "get_roster_with_cache", roster_with_cache)
    monkeypatch.setattr(
        app,
        "load_prediction_cache",
        lambda game_date, team, opponent: {
            "prediction_rows": [{"player": "Victor Wembanyama", "stat_type": "PTS"}]
        },
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("selected-team-only predictions should not run when full cached rows exist")

    monkeypatch.setattr(app, "run_roster_predictions", fail_if_called)

    predictions, status = app.collect_betting_predictions()

    assert predictions == [{"player": "Victor Wembanyama", "stat_type": "PTS"}]
    assert status["rosters"] == {"NYK": "LIVE", "SAS": "UNAVAILABLE"}
    assert status["predictions"] == "CACHE"
