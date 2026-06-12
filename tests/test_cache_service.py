from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cache_service
import roster_service


def sample_roster(prefix="Player"):
    return [f"{prefix} {index}" for index in range(1, 9)]


def sample_prediction_rows(teams=("NYK", "SAS")):
    key_players = {
        "NYK": ["Jalen Brunson", "Karl-Anthony Towns", "OG Anunoby", "Josh Hart", "Mikal Bridges"],
        "SAS": ["Victor Wembanyama", "De'Aaron Fox", "Stephon Castle", "Dylan Harper", "Devin Vassell"],
    }
    rows = []
    for team in teams:
        players = key_players.get(team, []) + [f"{team} Player {index}" for index in range(1, 16)]
        for player in players:
            for stat in ("PTS", "REB", "AST"):
                rows.append({
                    "player": player,
                    "team": team,
                    "stat_type": stat,
                    "projection": 10.0,
                    "low_range": 8.0,
                    "high_range": 12.0,
                })
    return rows


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
    rows = sample_prediction_rows()

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
    rows = sample_prediction_rows()

    cache_service.save_prediction_cache("2026-06-10", "NYK", "SAS", rows)

    cache_path = tmp_path / "predictions_2026-06-10_NYK_SAS.json"
    assert cache_path.exists()
    assert "*" not in cache_path.name
    assert not set('<>:"/\\|?*').intersection(cache_path.name)

    cached = cache_service.load_prediction_cache("2026-06-10", "NYK", "SAS")

    assert cached["team"] == "NYK"
    assert cached["opponent"] == "SAS"
    assert cached["prediction_rows"] == rows


def test_collect_betting_predictions_does_not_use_prediction_cache_when_opponent_roster_missing(monkeypatch):
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

    def fail_if_called(*args, **kwargs):
        raise AssertionError("prediction cache should not be used in normal betting flow")

    monkeypatch.setattr(app, "load_prediction_cache", fail_if_called, raising=False)
    monkeypatch.setattr(
        app,
        "run_roster_predictions",
        lambda *args, **kwargs: {
            "prediction_rows": [{"player": "Jalen Brunson", "team": "NYK", "stat_type": "PTS", "projection": 20, "low_range": 18, "high_range": 22}],
            "failed": [],
        },
    )
    monkeypatch.setattr(app, "save_prediction_cache", lambda *args, **kwargs: None)

    predictions, status = app.collect_betting_predictions()

    assert predictions == [{"player": "Jalen Brunson", "team": "NYK", "stat_type": "PTS", "projection": 20, "low_range": 18, "high_range": 22}]
    assert status["rosters"] == {"NYK": "LIVE", "SAS": "UNAVAILABLE"}
    assert status["predictions"] == "LIVE"

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


def test_collect_betting_predictions_uses_generated_rows_for_cached_roster(monkeypatch, capsys):
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
            "prediction_rows": [{"player": "Cached 1", "team": "NYK", "stat_type": "PTS", "projection": 10, "low_range": 8, "high_range": 12}],
            "failed": [(player, "No regular season data found") for player in cached_roster_players[:4]],
        },
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("prediction cache should not be used in normal betting flow")

    monkeypatch.setattr(app, "load_prediction_cache", fail_if_called, raising=False)
    monkeypatch.setattr(app, "save_prediction_cache", lambda *args, **kwargs: None)

    predictions, status = app.collect_betting_predictions()

    assert predictions == [{"player": "Cached 1", "team": "NYK", "stat_type": "PTS", "projection": 10, "low_range": 8, "high_range": 12}]
    assert status["predictions"] == "LIVE"
    assert "Cached roster appears unreliable" not in capsys.readouterr().out

def test_invalid_prediction_cache_missing_brunson_for_nyk_gets_rejected():
    rows = [row for row in sample_prediction_rows(("NYK",)) if row["player"] != "Jalen Brunson"]

    is_valid, reason = cache_service.validate_prediction_cache(rows, expected_teams=["NYK"])

    assert is_valid is False
    assert "Jalen Brunson" in reason


def test_invalid_prediction_cache_is_deleted_automatically(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cache_service, "CACHE_DIR", tmp_path)
    path = cache_service._prediction_cache_path("2026-06-08", "NYK", "SAS")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "game_date": "2026-06-08",
        "team": "NYK",
        "opponent": "SAS",
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "prediction_rows": [{"player": "Jalen Brunson"}],
    }), encoding="utf-8")

    cached = cache_service.load_prediction_cache("2026-06-08", "NYK", "SAS")

    assert cached["invalid_deleted"] is True
    assert not path.exists()
    assert "Invalid cache detected" in capsys.readouterr().out


def test_valid_two_team_nyk_sas_prediction_cache_passes():
    rows = sample_prediction_rows()

    is_valid, reason = cache_service.validate_prediction_cache(rows, expected_teams=["NYK", "SAS"])

    assert is_valid is True
    assert reason == "valid"


def test_bad_roster_cache_is_not_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_service, "CACHE_DIR", tmp_path)

    result = cache_service.save_roster_cache("NYK", ["Bad Player"])

    assert result is None
    assert not (tmp_path / "roster_NYK.json").exists()


def test_cache_healing_does_not_delete_gitignore_or_gitkeep(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_service, "CACHE_DIR", tmp_path)
    (tmp_path / ".gitignore").write_text("*\n", encoding="utf-8")
    (tmp_path / ".gitkeep").write_text("", encoding="utf-8")

    assert cache_service.clear_cache_file(tmp_path / ".gitignore") is False
    assert cache_service.clear_cache_file(tmp_path / ".gitkeep") is False
    assert (tmp_path / ".gitignore").exists()
    assert (tmp_path / ".gitkeep").exists()


def test_get_roster_with_cache_normalizes_before_lookup(monkeypatch):
    calls = []

    def fake_live(team, season="2025-26", timeout=45):
        calls.append(team)
        return 1, sample_roster("Knicks")

    monkeypatch.setattr(roster_service, "get_team_roster", fake_live)
    monkeypatch.setattr(roster_service, "save_roster_cache", lambda *args, **kwargs: None)

    _, roster, status = roster_service.get_roster_with_cache("NY")

    assert calls == ["NYK"]
    assert roster == sample_roster("Knicks")
    assert status == "LIVE"


def test_get_roster_with_cache_retries_when_first_live_call_fails(monkeypatch):
    calls = []

    def flaky_live(team, season="2025-26", timeout=45):
        calls.append(team)
        if len(calls) == 1:
            raise ValueError("timeout")
        return 1, sample_roster("Knicks")

    monkeypatch.setattr(roster_service, "get_team_roster", flaky_live)
    monkeypatch.setattr(roster_service, "save_roster_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(roster_service.time, "sleep", lambda *_args: None)

    _, roster, status = roster_service.get_roster_with_cache("NYK")

    assert len(calls) == 2
    assert roster == sample_roster("Knicks")
    assert status == "LIVE"


def test_cache_is_used_only_after_live_retries_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_service, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(roster_service.cache_service, "CACHE_DIR", tmp_path)
    cache_service.save_roster_cache("SAS", sample_roster("Spur"))
    calls = []

    def fail_live(team, season="2025-26", timeout=45):
        calls.append(team)
        raise ValueError("timeout")

    monkeypatch.setattr(roster_service, "get_team_roster", fail_live)
    monkeypatch.setattr(roster_service.time, "sleep", lambda *_args: None)

    _, roster, status = roster_service.get_roster_with_cache("SA")

    assert calls == ["SAS", "SAS"]
    assert roster == sample_roster("Spur")
    assert status == "CACHE"


def test_invalid_roster_cache_is_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_service, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(roster_service.cache_service, "CACHE_DIR", tmp_path)
    (tmp_path / "roster_NYK.json").write_text(json.dumps({
        "team_abbr": "NYK",
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "roster": ["Bad"],
    }), encoding="utf-8")
    monkeypatch.setattr(roster_service, "get_team_roster", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("timeout")))
    monkeypatch.setattr(roster_service.time, "sleep", lambda *_args: None)

    _, roster, status = roster_service.get_roster_with_cache("NYK")

    assert roster == []
    assert status == "INVALID-DELETED"
    assert not (tmp_path / "roster_NYK.json").exists()


def test_debug_roster_mode_prints_useful_status(monkeypatch, capsys):
    monkeypatch.setattr(roster_service, "get_team_roster", lambda *args, **kwargs: (1, sample_roster("Knicks")))
    monkeypatch.setattr(roster_service, "save_roster_cache", lambda *args, **kwargs: None)

    result = roster_service.debug_roster_lookup("NY")

    output = capsys.readouterr().out
    assert result["team"] == "NYK"
    assert "Normalized team abbreviation: NYK" in output
    assert "Live roster result count: 8" in output
    assert "Cache status: LIVE" in output
    assert "Validation result: PASS" in output


def test_app_stops_when_no_roster_instead_of_using_prediction_cache(tmp_path, monkeypatch):
    import app

    monkeypatch.setattr(cache_service, "CACHE_DIR", tmp_path)
    rows = sample_prediction_rows()
    cache_service.save_prediction_cache("2026-06-08", "NYK", "SAS", rows)
    context = {
        "opponent": "SAS",
        "home": True,
        "playoff_game": True,
        "game_date": "2026-06-08",
        "game_id": "1",
    }
    monkeypatch.setattr(
        app,
        "_load_roster_for_betting",
        lambda season="2025-26": (
            [],
            "NYK",
            context,
            {
                "rosters": {"NYK": "UNAVAILABLE"},
                "predictions": "LIVE",
                "opponent_unavailable": False,
                "cached_roster_players": [],
                "prediction_health": None,
            },
        ),
    )

    predictions, status = app.collect_betting_predictions()

    assert predictions == []
    assert status["predictions"] == "UNAVAILABLE"

def test_startup_health_check_deletes_invalid_cache(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cache_service, "CACHE_DIR", tmp_path)
    (tmp_path / ".gitkeep").write_text("", encoding="utf-8")
    (tmp_path / "bad:name.json").write_text("{}", encoding="utf-8")
    (tmp_path / "roster_NYK.json").write_text(json.dumps({
        "team_abbr": "NYK",
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "roster": ["Bad"],
    }), encoding="utf-8")

    report = cache_service.run_health_check()

    assert report["removed"] == 2
    assert not (tmp_path / "bad:name.json").exists()
    assert not (tmp_path / "roster_NYK.json").exists()
    assert (tmp_path / ".gitkeep").exists()
    assert "Startup health check: removed 2 invalid cache file(s)." in capsys.readouterr().out


def test_clear_cache_flag_clears_cache_without_deleting_keep_files(tmp_path, monkeypatch, capsys):
    import app

    monkeypatch.setattr(cache_service, "CACHE_DIR", tmp_path)
    (tmp_path / ".gitignore").write_text("*\n", encoding="utf-8")
    (tmp_path / ".gitkeep").write_text("", encoding="utf-8")
    (tmp_path / "roster_NYK.json").write_text("{}", encoding="utf-8")

    app.main(["--clear-cache"])

    assert (tmp_path / ".gitignore").exists()
    assert (tmp_path / ".gitkeep").exists()
    assert not (tmp_path / "roster_NYK.json").exists()
    assert "Removed 1 cache file(s)." in capsys.readouterr().out


def test_debug_roster_flag_runs_for_nyk(monkeypatch, capsys):
    import app

    monkeypatch.setattr(roster_service, "get_team_roster", lambda *args, **kwargs: (1, sample_roster("Knicks")))
    monkeypatch.setattr(roster_service, "save_roster_cache", lambda *args, **kwargs: None)

    app.main(["--debug-roster", "NYK"])

    output = capsys.readouterr().out
    assert "Normalized team abbreviation: NYK" in output
    assert "Live roster result count: 8" in output


def test_main_menu_no_longer_includes_cache_or_debug_options(capsys):
    import app

    app.print_main_menu()

    output = capsys.readouterr().out
    assert "1. Single Player Prediction" in output
    assert "6. Grade Predictions" in output
    assert "7. Clear Cache" not in output
    assert "8. Debug Roster Lookup" not in output


def test_bad_prediction_cache_is_not_used_or_auto_cleared_during_betting_workflow(tmp_path, monkeypatch):
    import app

    monkeypatch.setattr(cache_service, "CACHE_DIR", tmp_path)
    bad_path = cache_service._prediction_cache_path("2026-06-08", "NYK", "SAS")
    bad_path.parent.mkdir(parents=True, exist_ok=True)
    bad_path.write_text(json.dumps({
        "game_date": "2026-06-08",
        "team": "NYK",
        "opponent": "SAS",
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "prediction_rows": [{"player": "Only Player"}],
    }), encoding="utf-8")
    context = {
        "opponent": "SAS",
        "home": True,
        "playoff_game": True,
        "game_date": "2026-06-08",
        "game_id": "1",
    }
    monkeypatch.setattr(
        app,
        "_load_roster_for_betting",
        lambda season="2025-26": (
            [],
            "NYK",
            context,
            {
                "rosters": {"NYK": "UNAVAILABLE"},
                "predictions": "LIVE",
                "opponent_unavailable": False,
                "cached_roster_players": [],
                "prediction_health": None,
            },
        ),
    )

    predictions, status = app.collect_betting_predictions()

    assert predictions == []
    assert status["predictions"] == "UNAVAILABLE"
    assert bad_path.exists()
