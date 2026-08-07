from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.services.readiness_service import database_health, readiness_report
from backend.app.services.search_service import search_catalog


def test_database_health_opens_reads_and_reports_schema_without_secrets():
    result = database_health()
    assert result["connectionOpened"] is True
    assert result["readSucceeded"] is True
    assert result["status"] in {"healthy", "degraded"}
    assert result["latencyMs"] >= 0
    assert "password" not in str(result).lower()


def test_readiness_endpoint_reports_real_dependencies():
    response = TestClient(app).get("/api/readiness")
    assert response.status_code == 200
    payload = response.json()
    assert payload["database"]["readSucceeded"] is True
    assert payload["predictionStore"]["readableArtifacts"] > 0
    assert payload["gameStatusSource"]["liveProbePerformed"] is False


def test_search_handles_team_abbreviation_full_name_case_and_partial_player():
    assert any(item["name"] == "New England Patriots" for item in search_catalog("PATRIOTS")["items"])
    assert any(item["abbreviation"] == "NE" for item in search_catalog("ne")["items"] if item["type"] == "team")
    assert any("Mahomes" in item["name"] for item in search_catalog("mahom")["items"] if item["type"] == "player")


def test_search_distinguishes_no_results_from_service_failure():
    result = search_catalog("definitely-not-a-team-or-player")
    assert result["items"] == []
    assert result["counts"] == {"teams": 0, "players": 0, "games": 0}


def test_normalized_validation_error_contract():
    response = TestClient(app).get("/api/search?q=x")
    assert response.status_code == 422
    assert response.json()["error"] == {"code": "VALIDATION_ERROR", "message": "The request did not match the expected schema.", "retryable": False}


def test_compact_runtime_catalogs_work_when_research_snapshots_are_not_deployed(tmp_path, monkeypatch):
    import json
    from backend.app.services import nfl_context_service, search_service

    data = tmp_path / "data"
    data.mkdir()
    (data / "nfl_team_context_history.json").write_text(json.dumps([{"game_id": "espn-1", "team": "DET", "season": 2025, "record_role": "completed_game_history", "points_for": 24, "points_against": 17}]))
    (data / "nfl_player_search_index.json").write_text(json.dumps([{"id": "3139477", "name": "Patrick Mahomes", "team": "KC", "position": "QB", "season": 2026}]))
    monkeypatch.setattr(nfl_context_service, "BASE_DIR", tmp_path)
    monkeypatch.setattr(search_service, "BASE_DIR", tmp_path)
    search_service._player_index.cache_clear()
    rows, source = nfl_context_service._season_rows(2025)
    assert rows[0]["team"] == "DET" and source.name == "nfl_team_context_history.json"
    assert any(item["name"] == "Patrick Mahomes" for item in search_service.search_catalog("mahom")["items"])
    search_service._player_index.cache_clear()
