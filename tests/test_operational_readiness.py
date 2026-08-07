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
