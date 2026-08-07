import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services.game_status_service import cache_ttl_seconds, lifecycle_cache_ttl, monotonic_status, normalize_game_status
from backend.app.services.nfl_game_service import (
    canonical_game_id,
    clear_nfl_game_cache,
    get_nfl_game_detail,
    refresh_nfl_game_detail,
)

FIXTURE = json.loads(Path("tests/fixtures/nfl_game_detail_completed_preseason.json").read_text())


@pytest.fixture(autouse=True)
def clear_cache():
    clear_nfl_game_cache()


@pytest.mark.parametrize(
    ("name", "detail", "completed", "expected"),
    [
        ("STATUS_SCHEDULED", "Scheduled", False, "scheduled"),
        ("pre", None, False, "pregame"),
        ("STATUS_IN_PROGRESS", "3rd Quarter", False, "live"),
        ("STATUS_HALFTIME", "Halftime", False, "halftime"),
        ("STATUS_FINAL", "Final", True, "final"),
        ("STATUS_FINAL", "Final/OT", True, "final-OT"),
        ("STATUS_POSTPONED", "Postponed", False, "postponed"),
        ("STATUS_CANCELED", "Canceled", False, "canceled"),
        ("something-new", "Awaiting provider", False, "unknown"),
    ],
)
def test_status_normalization(name, detail, completed, expected):
    assert normalize_game_status(name, detail, completed) == expected


def test_final_is_monotonic_and_ttl_is_lifecycle_aware():
    assert monotonic_status("final", "scheduled", "2026-08-08T03:10:00Z", "2026-08-08T02:00:00Z") == "final"
    assert cache_ttl_seconds("live") < cache_ttl_seconds("scheduled") < cache_ttl_seconds("final")
    assert lifecycle_cache_ttl("scheduled", "2026-08-10T12:00:00Z", "2026-08-07T12:00:00Z") == 1800
    assert lifecycle_cache_ttl("scheduled", "2026-08-07T12:30:00Z", "2026-08-07T12:00:00Z") == 60
    assert lifecycle_cache_ttl("final", "2026-08-07T10:00:00Z", "2026-08-07T12:00:00Z", stats_complete=False) == 60


def test_canonical_game_id_accepts_existing_prefix_and_rejects_slugs():
    assert canonical_game_id("espn-401000001") == "401000001"
    with pytest.raises(ValueError):
        canonical_game_id("mia-at-buf")


def test_completed_preseason_fixture_has_distinct_phase_key_and_comparison():
    detail = get_nfl_game_detail("401000001", fetcher=lambda _: FIXTURE)
    assert detail["game"]["status"] == "final"
    assert detail["game"]["seasonPhase"] == "preseason"
    assert detail["game"]["phaseWeekKey"] == "2026:preseason:w1"
    assert detail["teamContext"]["label"] == "PREGAME CONTEXT"
    assert detail["actuals"]["available"] is True
    assert detail["comparison"]["available"] is True
    assert detail["comparison"]["items"][0]["actual"] == 62
    assert detail["sources"]["paidProviderContacted"] is False
    assert detail["reconciliation"]["repairAction"] == "ADVANCE_TO_FINAL_FROM_SCORE_AND_BOX_SCORE"


def test_stale_scheduled_game_with_final_score_and_stats_repairs_to_final():
    scheduled = json.loads(json.dumps(FIXTURE))
    status = scheduled["header"]["competitions"][0]["status"]
    status["type"] = {"name": "STATUS_SCHEDULED", "detail": "Scheduled", "completed": False}
    status["lastUpdated"] = "2026-08-08T00:00:00Z"
    detail = get_nfl_game_detail("401000001", fetcher=lambda _: scheduled)
    assert detail["game"]["status"] == "final"
    assert detail["game"]["awayScore"] == 17 and detail["game"]["homeScore"] == 24
    assert detail["actuals"]["available"] is True
    assert detail["reconciliation"]["repairAction"] == "ADVANCE_TO_FINAL_FROM_SCORE_AND_BOX_SCORE"
    assert "STALE_SCHEDULE_STATUS" in detail["reconciliation"]["reasonCodes"]


def test_manual_refresh_is_debounced_and_coalesced():
    calls = []
    def fetcher(_):
        calls.append(1)
        return FIXTURE
    first = refresh_nfl_game_detail("401000001", fetcher=fetcher)
    second = refresh_nfl_game_detail("401000001", fetcher=fetcher)
    assert first["refresh"]["coalesced"] is False
    assert second["refresh"]["coalesced"] is True
    assert len(calls) == 1


def test_simultaneous_manual_refreshes_share_one_upstream_request():
    calls = []
    results = []
    def fetcher(_):
        calls.append(1)
        time.sleep(0.05)
        return FIXTURE
    threads = [threading.Thread(target=lambda: results.append(refresh_nfl_game_detail("401000001", fetcher=fetcher))) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(calls) == 1
    assert sorted(result["refresh"]["coalesced"] for result in results) == [False, True]


def test_game_detail_route_uses_canonical_id(monkeypatch):
    monkeypatch.setattr("backend.app.main.get_nfl_game_detail", lambda game_id: {"game": {"id": game_id, "status": "scheduled"}})
    response = TestClient(app).get("/api/nfl/games/401000001")
    assert response.status_code == 200
    assert response.json()["game"]["id"] == "401000001"


def test_upcoming_game_with_predictions_keeps_actuals_separate():
    upcoming = json.loads(json.dumps(FIXTURE))
    competition = upcoming["header"]["competitions"][0]
    competition["date"] = "2026-08-20T00:00:00Z"
    competition["status"]["type"] = {"name": "STATUS_SCHEDULED", "detail": "Scheduled", "completed": False}
    for competitor in competition["competitors"]:
        competitor.pop("score", None)
    upcoming["boxscore"] = {}
    detail = get_nfl_game_detail("401000001", fetcher=lambda _: upcoming)
    assert detail["game"]["status"] == "scheduled"
    assert detail["predictions"]["available"] is True
    assert detail["actuals"]["available"] is False
    assert detail["comparison"]["available"] is False


def test_upcoming_preseason_without_artifact_returns_empty_prediction_state():
    upcoming = json.loads(json.dumps(FIXTURE))
    upcoming["_smartbetFixture"].pop("predictions")
    competition = upcoming["header"]["competitions"][0]
    competition["date"] = "2026-08-20T00:00:00Z"
    competition["status"]["type"] = {"name": "STATUS_SCHEDULED", "detail": "Scheduled", "completed": False}
    competition["competitors"][0].pop("score", None)
    competition["competitors"][1].pop("score", None)
    upcoming["boxscore"] = {}
    detail = get_nfl_game_detail("401000001", fetcher=lambda _: upcoming)
    assert detail["predictions"]["available"] is False
    assert "No frozen System A preseason prediction artifact" in detail["predictions"]["reason"]


def test_live_game_can_expose_partial_player_stats_without_becoming_final():
    live = json.loads(json.dumps(FIXTURE))
    competition = live["header"]["competitions"][0]
    competition["status"]["type"] = {"name": "STATUS_IN_PROGRESS", "detail": "2nd Quarter", "completed": False}
    competition["competitors"][0].pop("score", None)
    competition["competitors"][1].pop("score", None)
    detail = get_nfl_game_detail("401000001", fetcher=lambda _: live)
    assert detail["game"]["status"] == "live"
    assert detail["actuals"]["available"] is True
    assert detail["comparison"]["available"] is False


def test_prior_season_context_fallback_and_missing_stats_are_reported_honestly():
    payload = json.loads(json.dumps(FIXTURE))
    payload["_smartbetFixture"].pop("teamContext")
    payload["boxscore"] = {}
    detail = get_nfl_game_detail("401000001", fetcher=lambda _: payload)
    assert detail["teamContext"]["available"] is True
    assert detail["teamContext"]["requestedSeason"] == 2026
    assert detail["teamContext"]["contextSeasonUsed"] == 2025
    assert detail["teamContext"]["fallbackUsed"] is True
    assert detail["teamContext"]["fallbackReason"] == "INSUFFICIENT_CURRENT_SEASON_SAMPLE"
    assert detail["actuals"]["available"] is False
    assert detail["dataAvailability"]["contextAvailable"] is True


def test_upcoming_empty_provider_boxscore_templates_are_not_actual_results():
    upcoming = json.loads(json.dumps(FIXTURE))
    competition = upcoming["header"]["competitions"][0]
    competition["date"] = "2026-08-20T00:00:00Z"
    competition["status"]["type"] = {"name": "STATUS_SCHEDULED", "detail": "Scheduled", "completed": False}
    for competitor in competition["competitors"]:
        competitor.pop("score", None)
    upcoming["boxscore"]["teams"] = [
        {"team": {"abbreviation": "NE"}, "statistics": []},
        {"team": {"abbreviation": "NYG"}, "statistics": []},
    ]
    upcoming["boxscore"]["players"] = []
    detail = get_nfl_game_detail("401000001", fetcher=lambda _: upcoming)
    assert detail["game"]["status"] == "scheduled"
    assert detail["actuals"]["available"] is False
    assert detail["actuals"]["teamStats"] == []
    assert detail["actuals"]["providerDataAvailable"] is False


def test_failed_forced_refresh_preserves_last_known_good_cache():
    initial = get_nfl_game_detail("401000001", fetcher=lambda _: FIXTURE)
    with pytest.raises(RuntimeError):
        get_nfl_game_detail("401000001", force=True, fetcher=lambda _: (_ for _ in ()).throw(RuntimeError("provider down")))
    cached = get_nfl_game_detail("401000001", fetcher=lambda _: {})
    assert cached["game"] == initial["game"]


def test_missing_game_id_returns_not_found(monkeypatch):
    from backend.app.services.nfl_game_service import GameNotFoundError
    monkeypatch.setattr("backend.app.main.get_nfl_game_detail", lambda _game_id: (_ for _ in ()).throw(GameNotFoundError()))
    response = TestClient(app).get("/api/nfl/games/401999999")
    assert response.status_code == 404


def test_preseason_and_regular_week_one_cache_keys_do_not_collide():
    preseason = get_nfl_game_detail("401000001", fetcher=lambda _: FIXTURE)
    regular_payload = json.loads(json.dumps(FIXTURE))
    regular_payload["header"]["season"]["type"] = {"id": 2, "name": "Regular Season"}
    regular = get_nfl_game_detail("401000002", fetcher=lambda _: regular_payload)
    assert preseason["game"]["phaseWeekKey"] == "2026:preseason:w1"
    assert regular["game"]["phaseWeekKey"] == "2026:regular_season:w1"
