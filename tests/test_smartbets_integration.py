from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


def test_static_history_route_is_not_shadowed_by_game_id(monkeypatch):
    from backend.app import main

    main.app.dependency_overrides[main.require_full_access] = lambda: {"id": 1}
    monkeypatch.setattr(main, "historical_games", lambda *args: [])
    try:
        response = TestClient(main.app).get("/api/nfl/games/history?season=2026")
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert "policy" in response.json()


def test_laptop_and_home_routes_coexist():
    from backend.app.main import app

    paths = app.openapi()["paths"]
    for path in ("/api/readiness", "/api/search", "/api/games/refresh", "/api/nfl/games/{game_id}",
                 "/api/nfl/week", "/api/nfl/context", "/api/nfl/games/history",
                 "/api/internal/nfl/experiments/{season}/{season_type}/{week}",
                 "/api/internal/operations"):
        assert path in paths
    assert (ROOT / "frontend/app/analyze/classic/page.jsx").exists()
    assert (ROOT / "frontend/app/internal/experiments/week3/page.jsx").exists()
    assert (ROOT / "frontend/app/internal/operations/page.jsx").exists()


def test_app_lifespan_and_readiness_start_with_isolated_database():
    from backend.app.main import app

    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["database"]["readSucceeded"] is True
        assert client.get("/api/readiness").status_code == 200


def test_week3_expected_hash_is_preserved_and_missing_evidence_fails_closed():
    from backend.app.services import nfl_experiment_service as experiment
    from backend.app.services.nfl_product_service import _initialize_predictions

    expected = "a8a405ba262ef59bedb1b7bfcdf1ca4a7c0bf78cafe4b0ff0c1268b7d8b2412a"
    assert experiment.WEEK3_EXPECTED_HASH == expected
    _initialize_predictions()
    result = experiment.experiment_integrity(2026, "preseason", 3)
    assert result["expectedHash"] == expected
    assert result["frozenPredictionCount"] == 0
    assert result["verified"] is False  # Never fabricate the missing authoritative rows.
