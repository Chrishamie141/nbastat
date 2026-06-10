from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cache_service
import roster_service


def sample_roster(prefix="Player"):
    return [f"{prefix} {index}" for index in range(1, 9)]


def test_roster_cache_falls_back_when_live_lookup_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cache_service, "CACHE_DIR", tmp_path)
    cache_service.save_roster_cache("SAS", sample_roster("Spur"))

    def fail_live_lookup(*args, **kwargs):
        raise ValueError("simulated timeout")

    monkeypatch.setattr(roster_service, "get_team_roster", fail_live_lookup)
    team_id, roster, status = roster_service.get_roster_with_cache("SAS")

    assert team_id is not None
    assert roster == sample_roster("Spur")
    assert status == "CACHE"
    assert "Using cached roster for SAS" in capsys.readouterr().out


def test_stale_roster_cache_is_used_when_live_lookup_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cache_service, "CACHE_DIR", tmp_path)
    stale_time = (datetime.now(timezone.utc) - timedelta(hours=80)).isoformat()
    (tmp_path / "roster_SAS.json").write_text(
        json.dumps({"team_abbr": "SAS", "cached_at": stale_time, "roster": sample_roster("Spur")}),
        encoding="utf-8",
    )

    def fail_live_lookup(*args, **kwargs):
        raise ValueError("simulated timeout")

    monkeypatch.setattr(roster_service, "get_team_roster", fail_live_lookup)
    _, roster, status = roster_service.get_roster_with_cache("SAS")

    assert roster == sample_roster("Spur")
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


def test_safe_cache_key_sanitizes_windows_filename_components():
    assert cache_service.safe_cache_key(None) == "unknown"
    assert cache_service.safe_cache_key("") == "unknown"
    assert cache_service.safe_cache_key("  NYK  ") == "NYK"
    assert cache_service.safe_cache_key("2026/06/10") == "2026_06_10"
    assert cache_service.safe_cache_key("New York: Knicks*") == "New_York_Knicks"


def test_prediction_cache_path_uses_windows_safe_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_service, "CACHE_DIR", tmp_path)

    path = cache_service._prediction_cache_path("2026-06-10", "NYK", "SAS")

    assert path == tmp_path / "predictions_2026-06-10_NYK_SAS.json"
    assert "*" not in path.name
    assert not set('<>:"/\\|?*').intersection(path.name)


def test_prediction_cache_round_trip_uses_windows_safe_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_service, "CACHE_DIR", tmp_path)
    rows = [{"player": "Jalen Brunson", "stat_type": "PTS", "projection": 28.4}]

    cache_service.save_prediction_cache("2026-06-10", "NYK", "SAS", rows)

    cache_path = tmp_path / "predictions_2026-06-10_NYK_SAS.json"
    assert cache_path.exists()
    assert "*" not in cache_path.name
    assert not set('<>:"/\\|?*').intersection(cache_path.name)

    cached = cache_service.load_prediction_cache("2026-06-10", "NYK", "SAS")

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


def test_validate_roster_cache_accepts_valid_dict_roster():
    roster = [
        {"player_name": f"Player {index}", "player_id": index}
        for index in range(1, 9)
    ]

    is_valid, reason = roster_service.validate_roster_cache(roster)

    assert is_valid is True
    assert reason == "valid"


def test_validate_roster_cache_accepts_valid_string_roster():
    is_valid, _ = roster_service.validate_roster_cache(sample_roster("Knicks"))

    assert is_valid is True


def test_validate_roster_cache_rejects_empty_roster():
    is_valid, reason = roster_service.validate_roster_cache([])

    assert is_valid is False
    assert "fewer than 8" in reason


def test_validate_roster_cache_rejects_malformed_roster():
    malformed_roster = ["123", "{}", "[]", "<bad>", "::::", "0", "99", "---"]

    is_valid, reason = roster_service.validate_roster_cache(malformed_roster)

    assert is_valid is False
    assert "malformed" in reason


def test_get_player_display_name_supports_multiple_formats():
    cases = [
        (" Jalen Brunson ", "Jalen Brunson"),
        ({"player_name": "OG Anunoby"}, "OG Anunoby"),
        ({"name": "Josh Hart"}, "Josh Hart"),
        ({"full_name": "Mikal Bridges"}, "Mikal Bridges"),
        ({"PLAYER": "Karl-Anthony Towns"}, "Karl-Anthony Towns"),
        ({"PLAYER_NAME": "Mitchell Robinson"}, "Mitchell Robinson"),
        ({"DISPLAY_FIRST_LAST": "Miles McBride"}, "Miles McBride"),
    ]

    for player, expected in cases:
        assert roster_service.get_player_display_name(player) == expected


def test_clear_cache_keeps_gitignore_and_gitkeep(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_service, "CACHE_DIR", tmp_path)
    (tmp_path / ".gitignore").write_text("*\n", encoding="utf-8")
    (tmp_path / ".gitkeep").write_text("", encoding="utf-8")
    (tmp_path / "roster_NYK.json").write_text("{}", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "predictions.json").write_text("{}", encoding="utf-8")

    removed_count = cache_service.clear_cache_files()

    assert removed_count == 2
    assert (tmp_path / ".gitignore").exists()
    assert (tmp_path / ".gitkeep").exists()
    assert not (tmp_path / "roster_NYK.json").exists()
    assert not (nested / "predictions.json").exists()


def test_collect_betting_predictions_prefers_cached_rows_when_cached_roster_unreliable(monkeypatch, capsys):
    import app

    context = {
        "opponent": "SAS",
        "home": True,
        "playoff_game": True,
        "game_date": "2026-06-08",
        "game_id": "1",
    }
    cached_roster_players = sample_roster("Cached")
    monkeypatch.setattr(
        app,
        "_load_roster_for_betting",
        lambda season="2025-26": (
            cached_roster_players,
            "NYK",
            context,
            {
                "rosters": {"NYK": "CACHE"},
                "predictions": "LIVE",
                "opponent_unavailable": False,
                "cached_roster_players": cached_roster_players,
            },
        ),
    )
    monkeypatch.setattr(
        app,
        "run_roster_predictions",
        lambda *args, **kwargs: {
            "prediction_rows": [{"player": "Cached 1", "stat_type": "PTS"}],
            "failed": [(player, "No regular season data found") for player in cached_roster_players[:4]],
        },
    )
    monkeypatch.setattr(
        app,
        "load_prediction_cache",
        lambda game_date, team, opponent: {
            "prediction_rows": [{"player": "Cached Prediction", "stat_type": "PTS"}]
        },
    )

    predictions, status = app.collect_betting_predictions()

    assert predictions == [{"player": "Cached Prediction", "stat_type": "PTS"}]
    assert status["predictions"] == "CACHE"
    assert "Cached roster appears unreliable" in capsys.readouterr().out
