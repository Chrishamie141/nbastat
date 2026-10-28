from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

import pandas as pd
import pytest

from backend.app.services.nba.availability import (
    current_availability, normalize_roster_payload, personnel_health,
    personnel_refresh_guard, refresh_personnel,
)
from backend.app.services.nba.data import player_log_health, safe_player_log


@pytest.fixture
def personnel_db(tmp_path, monkeypatch):
    path = tmp_path / "nba-personnel.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL_NON_POOLING", raising=False)
    return path


def roster_payload(timestamp="2026-10-25T12:00:00Z"):
    return {
        "timestamp": timestamp,
        "season": {"displayName": "2026-27"},
        "athletes": [
            {"id": "1", "displayName": "Healthy Unknown", "jersey": "1",
             "position": {"abbreviation": "G"},
             "status": {"name": "Active"}, "injuries": []},
            {"id": "2", "displayName": "Day To Day", "jersey": "2",
             "position": {"abbreviation": "F"},
             "status": {"name": "Active"},
             "injuries": [{"status": "Day-To-Day", "date": "2026-10-25T11:00:00Z"}]},
        ],
    }


def test_normalization_never_equates_missing_injury_with_available():
    rows, metadata = normalize_roster_payload(
        team_code="NY", season="2026-27", payload=roster_payload(),
        retrieved_at="2026-10-25T12:05:00Z",
    )
    assert metadata["rowCount"] == 2
    assert rows[0]["availability_status"] == "NO_INJURY_REPORTED"
    assert rows[0]["injury_status"] == "NOT_REPORTED"
    assert rows[1]["availability_status"] == "QUESTIONABLE"
    assert rows[1]["injury_observed_at"] == "2026-10-25T11:00:00Z"


def test_provider_season_mismatch_is_rejected():
    payload = roster_payload()
    payload["season"]["displayName"] = "2025-26"
    with pytest.raises(ValueError, match="season mismatch"):
        normalize_roster_payload(team_code="NY", season="2026-27", payload=payload)


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_refresh_is_idempotent_and_health_becomes_stale(personnel_db):
    def get(url, timeout):
        assert "teams/ny/roster" in url
        return Response(roster_payload())

    first = refresh_personnel(season="2026-27", team_codes=["NY"], http_get=get, max_workers=1)
    second = refresh_personnel(season="2026-27", team_codes=["NY"], http_get=get, max_workers=1)
    assert first["status"] == second["status"] == "FRESH"
    assert len(current_availability(season="2026-27")) == 2
    with sqlite3.connect(personnel_db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM nba_player_availability_snapshots").fetchone()[0] == 2
    fresh = personnel_health("2026-27", at=datetime(2026, 10, 25, 13, tzinfo=timezone.utc))
    stale = personnel_health("2026-27", at=datetime(2026, 10, 27, 13, tzinfo=timezone.utc))
    assert fresh["availability"]["counts"]["FRESH"] == 1
    assert fresh["availability"]["missingTeams"] == 29
    assert stale["availability"]["counts"]["STALE"] == 1


def test_personnel_refresh_guard_rejects_overlap(personnel_db):
    with personnel_refresh_guard() as first:
        assert first is True
        with personnel_refresh_guard() as second:
            assert second is False
    with personnel_refresh_guard() as after_release:
        assert after_release is True


def test_team_failure_is_persisted_not_silently_treated_as_empty(personnel_db):
    def fail(_url, timeout):
        raise TimeoutError("provider timeout")

    report = refresh_personnel(season="2026-27", team_codes=["NY"], http_get=fail, max_workers=1)
    health = personnel_health("2026-27")
    assert report["status"] == "ERROR"
    assert report["errors"] == [{"team": "NY", "errorCode": "TimeoutError"}]
    assert health["availability"]["counts"]["ERROR"] == 1


def test_player_log_provider_failure_has_explicit_health(monkeypatch):
    class Broken:
        def __init__(self, **_kwargs):
            raise TimeoutError("timeout")

    monkeypatch.setattr("backend.app.services.nba.data.playergamelog.PlayerGameLog", Broken)
    frame = safe_player_log("1", "2026-27", "Regular Season")
    health = player_log_health(frame)
    assert frame.empty
    assert health["status"] == "ERROR"
    assert health["errorCode"] == "TimeoutError"
    assert health["missingMeansAvailable"] is False


def test_player_log_success_tracks_latest_game(monkeypatch):
    class Endpoint:
        def __init__(self, **_kwargs):
            pass

        def get_data_frames(self):
            return [pd.DataFrame({"GAME_DATE": ["2026-10-20", "2026-10-24"], "PTS": [10, 20]})]

    monkeypatch.setattr("backend.app.services.nba.data.playergamelog.PlayerGameLog", Endpoint)
    health = player_log_health(safe_player_log("1", "2026-27", "Regular Season"))
    assert health["status"] == "FRESH"
    assert health["rowCount"] == 2
    assert health["dataObservedAt"].startswith("2026-10-24")
