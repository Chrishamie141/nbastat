from __future__ import annotations

from types import SimpleNamespace

from models import DifficultyLevel, SportType


def _prediction_result():
    leg = SimpleNamespace(
        sport=SportType.NFL,
        player="Test Player",
        team="BUF",
        stat_type="REC_YDS",
        line=64.5,
        odds=-110,
        prediction="Test Player over 64.5 REC_YDS",
        confidence=68.0,
        notes="Live provider projection.",
    )
    parlay = SimpleNamespace(
        sport=SportType.NFL,
        difficulty=DifficultyLevel.BALANCED,
        legs=[leg],
        notes="test",
        created_at="2026-08-03T12:00:00+00:00",
    )
    return SimpleNamespace(
        parlay=parlay,
        combined_probability=.68,
        estimated_odds=147,
        result_status="pending",
        notes="test",
    )


def test_web_prediction_survives_history_save_failure(monkeypatch):
    import backend.app.main as api

    monkeypatch.setattr(api, "build_nfl_parlay", lambda difficulty, team=None: _prediction_result())
    monkeypatch.setattr(api, "save_web_parlay", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("read only")))
    monkeypatch.setattr(api, "get_sports_mode", lambda: SimpleNamespace(phaseByLeague={"nfl": "preseason"}))

    response = api.nfl_parlay({"difficulty": "BALANCED"}, user={"id": 7})

    assert response["legs"][0]["prediction"] == "Test Player over 64.5 REC_YDS"
    assert response["saveStatus"] == "prediction generated; history save unavailable"
    assert response["modelVersion"] == "nfl_player_prop_matchup_v2"
    assert response["modelStatus"]["serving"] == "LATEST_DEPLOYABLE"
    assert response["modelStatus"]["researchPolicyState"] == "FROZEN_SHADOW_ONLY"


def test_web_parlay_history_uses_configured_database(monkeypatch, tmp_path):
    from backend.app.services.parlay_history_service import load_web_parlays, save_web_parlay

    database = tmp_path / "web.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database.as_posix()}")

    row_id = save_web_parlay(_prediction_result(), user_id=11, model_version="model-test")
    own_rows = load_web_parlays(user_id=11)
    other_rows = load_web_parlays(user_id=12)

    assert row_id == 1
    assert own_rows[0]["model_version"] == "model-test"
    assert own_rows[0]["user_id"] == 11
    assert other_rows == []


def test_postgres_adapter_translates_placeholders_and_sanitizes_url():
    from backend.app.database import _postgres_query, _postgres_url

    assert _postgres_query("SELECT * FROM users WHERE email LIKE '%@%' AND id=?") == (
        "SELECT * FROM users WHERE email LIKE '%%@%%' AND id=%s"
    )
    assert _postgres_url("postgres://u:p@example/db?sslmode=require&supa=pool") == (
        "postgresql://u:p@example/db?sslmode=require"
    )
