from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import pytest

import app
import roster_service


def test_get_team_id_nyk_and_sas():
    assert roster_service.get_team_id("NYK") == 1610612752
    assert roster_service.get_team_id("SAS") == 1610612759


class _FrameEndpoint:
    calls = []
    frame = pd.DataFrame({"PLAYER": ["Jalen Brunson", "OG Anunoby"]})

    def __init__(self, **kwargs):
        type(self).calls.append(kwargs)

    def get_data_frames(self):
        return [type(self).frame]


def test_get_team_roster_calls_commonteamroster_with_team_id_and_season(monkeypatch):
    _FrameEndpoint.calls = []
    monkeypatch.setattr(roster_service.commonteamroster, "CommonTeamRoster", _FrameEndpoint)

    team_id, roster = roster_service.get_team_roster("NY", season="2025-26", timeout=12)

    assert team_id == 1610612752
    assert roster == ["Jalen Brunson", "OG Anunoby"]
    call = _FrameEndpoint.calls[0]
    assert call["team_id"] == 1610612752
    assert call["season"] == "2025-26"
    assert call["league_id_nullable"] == "00"
    assert call["timeout"] == 12
    assert call["headers"]["x-nba-stats-origin"] == "stats"
    assert call["headers"]["x-nba-stats-token"] == "true"


def test_retry_logic_retries_on_timeout(monkeypatch):
    calls = []

    def flaky(team, season="2025-26", timeout=15):
        calls.append((team, season, timeout))
        if len(calls) < 3:
            raise TimeoutError("timed out")
        return 1610612752, ["Jalen Brunson"]

    monkeypatch.setattr(roster_service, "get_team_roster", flaky)
    monkeypatch.setattr(roster_service.time, "sleep", lambda *_args: None)

    team_id, roster, error = roster_service._try_live_roster_with_retries("NYK", "2025-26", 12)

    assert team_id == 1610612752
    assert roster == ["Jalen Brunson"]
    assert error is None
    assert calls == [("NYK", "2025-26", 12)] * 3


def test_fallback_live_endpoint_attempted_after_commonteamroster_fails(monkeypatch):
    common_calls = []
    fallback_calls = []

    def fail_common(team, season="2025-26", timeout=15):
        common_calls.append(team)
        raise TimeoutError("common failed")

    def fallback(team, season="2025-26", timeout=15):
        fallback_calls.append((team, season, timeout))
        return 1610612752, ["Jalen Brunson"]

    monkeypatch.setattr(roster_service, "get_team_roster", fail_common)
    monkeypatch.setattr(roster_service, "_live_roster_lookup_commonallplayers", fallback)
    monkeypatch.setattr(roster_service.time, "sleep", lambda *_args: None)

    team_id, roster, error = roster_service._try_live_roster_with_retries("NYK", "2025-26", 12)

    assert team_id == 1610612752
    assert roster == ["Jalen Brunson"]
    assert error is None
    assert common_calls == ["NYK", "NYK", "NYK"]
    assert fallback_calls == [("NYK", "2025-26", 12)]


def test_commonallplayers_filters_by_team_id(monkeypatch):
    class CommonAllPlayersEndpoint:
        calls = []

        def __init__(self, **kwargs):
            self.calls.append(kwargs)

        def get_data_frames(self):
            return [pd.DataFrame({
                "TEAM_ID": [1610612752, 1610612759],
                "DISPLAY_FIRST_LAST": ["Jalen Brunson", "Victor Wembanyama"],
            })]

    monkeypatch.setattr(roster_service.commonallplayers, "CommonAllPlayers", CommonAllPlayersEndpoint)

    team_id, roster = roster_service._live_roster_lookup_commonallplayers("NYK", season="2025-26", timeout=12)

    assert team_id == 1610612752
    assert roster == ["Jalen Brunson"]


def test_debug_roster_live_bypasses_cache(monkeypatch, capsys):
    def fail_cache(*args, **kwargs):
        raise AssertionError("cache should be bypassed")

    monkeypatch.setattr(roster_service, "load_roster_cache", fail_cache)
    monkeypatch.setattr(roster_service, "save_roster_cache", fail_cache)
    monkeypatch.setattr(roster_service, "get_team_roster", lambda *args, **kwargs: (1610612752, ["Jalen Brunson"]))

    result = roster_service.debug_roster_live_lookup("NYK", timeout=12)

    output = capsys.readouterr().out
    assert result["status"] == "LIVE"
    assert "Cache bypassed: yes" in output
    assert "Endpoint attempted: commonteamroster" in output
    assert "Team ID: 1610612752" in output
    assert "Result count: 1" in output


def test_debug_roster_live_cli_bypasses_cache(monkeypatch, capsys):
    monkeypatch.setattr(app, "run_health_check", lambda *args, **kwargs: None)
    monkeypatch.setattr(app, "debug_roster_live_lookup", lambda team, season="2025-26": print(f"uncached {team} {season}"))

    app.main(["--debug-roster-live", "NYK"])

    assert "uncached NYK 2025-26" in capsys.readouterr().out
