from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app


def _fake_result(player):
    return {
        "player": player,
        "regular_summary": {"games": 10},
        "playoff_summary": {"games": 2},
    }


def test_run_roster_predictions_passes_clean_strings_to_run_prediction(monkeypatch):
    received = []

    def fake_run_prediction(player_name, season, opponent, home, playoff_game):
        received.append(player_name)
        assert isinstance(player_name, str)
        return _fake_result(player_name)

    monkeypatch.setattr(app, "run_prediction", fake_run_prediction)
    monkeypatch.setattr(
        app,
        "prediction_rows_from_result",
        lambda result, team, context, save_to_db=True: [{"player": result["player"], "team": team}],
    )

    roster = [
        " Jalen Brunson ",
        {"player_name": "OG Anunoby", "player_id": 1},
        {"name": "Josh Hart", "id": 2},
        {"PLAYER": "Karl-Anthony Towns", "PLAYER_ID": 3},
        {"full_name": "Victor Wembanyama", "PERSON_ID": 4},
        {"DISPLAY_FIRST_LAST": "De'Aaron Fox", "PERSON_ID": 5},
    ]

    app.run_roster_predictions(roster, team="TEST", emit_output=False)

    assert received == [
        "Jalen Brunson",
        "OG Anunoby",
        "Josh Hart",
        "Karl-Anthony Towns",
        "Victor Wembanyama",
        "De'Aaron Fox",
    ]


def test_cached_roster_list_of_strings_works(monkeypatch):
    received = []
    monkeypatch.setattr(app, "run_prediction", lambda player_name, *args: received.append(player_name) or _fake_result(player_name))
    monkeypatch.setattr(app, "prediction_rows_from_result", lambda result, *args, **kwargs: [{"player": result["player"]}])

    app.run_roster_predictions(["Jalen Brunson", "OG Anunoby", "Josh Hart"], emit_output=False)

    assert received == ["Jalen Brunson", "OG Anunoby", "Josh Hart"]


def test_cached_roster_list_of_dicts_works(monkeypatch):
    received = []
    monkeypatch.setattr(app, "run_prediction", lambda player_name, *args: received.append(player_name) or _fake_result(player_name))
    monkeypatch.setattr(app, "prediction_rows_from_result", lambda result, *args, **kwargs: [{"player": result["player"]}])

    app.run_roster_predictions([
        {"player_name": "Karl-Anthony Towns"},
        {"PLAYER": "Victor Wembanyama"},
        {"name": "De'Aaron Fox"},
    ], emit_output=False)

    assert received == ["Karl-Anthony Towns", "Victor Wembanyama", "De'Aaron Fox"]


def test_prediction_cache_is_not_used_in_mode_5_normal_flow(monkeypatch):
    context = app.default_context()
    context.update({"game_date": "2026-06-08", "opponent": "SAS"})
    monkeypatch.setattr(
        app,
        "_load_roster_for_betting",
        lambda season="2025-26": (
            ["Jalen Brunson"],
            "NYK",
            context,
            {"rosters": {"NYK": "LIVE"}, "predictions": "LIVE", "opponent_unavailable": False, "cached_roster_players": [], "prediction_health": None},
        ),
    )

    def fail_if_cache_loaded(*args, **kwargs):
        raise AssertionError("prediction cache should not be loaded")

    monkeypatch.setattr(app, "load_prediction_cache", fail_if_cache_loaded, raising=False)
    monkeypatch.setattr(
        app,
        "run_roster_predictions",
        lambda *args, **kwargs: {"prediction_rows": [{"player": "Jalen Brunson", "team": "NYK"}], "failed": []},
    )
    monkeypatch.setattr(app, "save_prediction_cache", lambda *args, **kwargs: None)

    predictions, status = app.collect_betting_predictions()

    assert predictions == [{"player": "Jalen Brunson", "team": "NYK"}]
    assert status["predictions"] == "LIVE"


def test_debug_player_command_reports_prediction_path(monkeypatch, capsys):
    def fake_run_prediction(player_name, season="2025-26", opponent=None, home=False, playoff_game=False):
        assert player_name == "Jalen Brunson"
        return _fake_result(player_name)

    monkeypatch.setattr(app, "run_prediction", fake_run_prediction)

    app.main(["--debug-player", "Jalen Brunson"])

    output = capsys.readouterr().out
    assert "Player name received: Jalen Brunson" in output
    assert "Season: 2025-26" in output
    assert "Regular season data found: True" in output
    assert "Regular season games count: 10" in output
    assert "Playoff games count: 2" in output
