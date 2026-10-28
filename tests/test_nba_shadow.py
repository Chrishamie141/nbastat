from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

import pytest

from backend.app.services.nba.game_model import NBA_GAME_MODEL_VERSION
from backend.app.services.nba.lifecycle import settle_game, upsert_game
from backend.app.services.nba.production import (
    game_detail, generate_winner_predictions, performance, prediction_history,
)
from backend.app.services.nba.registry import NBA_GAME_RESEARCH_CANDIDATE_VERSION
from backend.app.services.nba.shadow import generate_shadow_predictions, load_artifact, shadow_comparison


@pytest.fixture
def shadow_db(tmp_path, monkeypatch):
    path = tmp_path / "nba-shadow.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL_NON_POOLING", raising=False)
    monkeypatch.setenv("NBA_SHADOW_ENABLED", "true")
    load_artifact.cache_clear()
    return path


def nba_game(game_id: str, kickoff: str, *, status: str, home_score=None, away_score=None):
    return {
        "game_id": game_id, "season": "2026-27", "kickoff_time": kickoff,
        "home_team": "BOS", "away_team": "NYK", "status": status,
        "home_score": home_score, "away_score": away_score, "provider": "espn",
        "provider_event_id": game_id, "provider_timestamp": "2026-10-25T10:00:00Z",
        "season_phase": "regular_season",
    }


def test_shadow_requires_explicit_feature_flag(shadow_db, monkeypatch):
    monkeypatch.setenv("NBA_SHADOW_ENABLED", "false")
    result = generate_shadow_predictions(
        season="2026-27", at=datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
    )
    assert result["enabled"] is False
    assert result["created"] == 0


def test_baseline_and_shadow_are_immutable_independent_and_idempotent(shadow_db):
    upsert_game(nba_game("prior", "2026-10-24T23:00:00Z", status="FINAL",
                         home_score=111, away_score=101))
    upsert_game(nba_game("future", "2026-10-26T23:00:00Z", status="SCHEDULED"))
    baseline = generate_winner_predictions(
        season="2026-27", at=datetime(2026, 10, 25, 12, 0, 0, tzinfo=timezone.utc)
    )
    shadow = generate_shadow_predictions(
        season="2026-27", at=datetime(2026, 10, 25, 12, 0, 1, tzinfo=timezone.utc)
    )
    repeated = generate_shadow_predictions(
        season="2026-27", at=datetime(2026, 10, 25, 12, 5, 0, tzinfo=timezone.utc)
    )
    assert baseline["created"] == 1
    assert shadow["enabled"] is True and shadow["created"] == 1
    assert repeated["created"] == 0 and repeated["alreadyPresent"] == 1

    rows = prediction_history(
        season="2026-27", user_id=0, game_id="future", include_shadow=True,
    )
    assert {row["modelVersion"] for row in rows} == {
        NBA_GAME_MODEL_VERSION, NBA_GAME_RESEARCH_CANDIDATE_VERSION,
    }
    generated = {row["modelVersion"]: row["createdAt"] for row in rows}
    assert generated[NBA_GAME_MODEL_VERSION] != generated[NBA_GAME_RESEARCH_CANDIDATE_VERSION]
    assert all(row["modelStatus"] == "RESEARCH_ONLY" for row in rows)
    assert all(row["wagerAuthorized"] is False for row in rows)
    customer_rows = prediction_history(season="2026-27", user_id=0, game_id="future")
    assert [row["modelVersion"] for row in customer_rows] == [NBA_GAME_MODEL_VERSION]
    detail = game_detail(season="2026-27", game_id="future")
    assert detail["prediction"]["modelVersion"] == NBA_GAME_MODEL_VERSION
    assert detail["shadowPredictions"] == []

    with sqlite3.connect(shadow_db) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="immutable NBA evidence"):
            connection.execute(
                "UPDATE nba_prediction_snapshots SET selection='MIA' WHERE game_id='future'"
            )


def test_settlement_and_metrics_track_models_without_replacing_baseline(shadow_db):
    upsert_game(nba_game("future", "2026-10-26T23:00:00Z", status="SCHEDULED"))
    generate_winner_predictions(
        season="2026-27", at=datetime(2026, 10, 25, 12, 0, 0, tzinfo=timezone.utc)
    )
    generate_shadow_predictions(
        season="2026-27", at=datetime(2026, 10, 25, 12, 0, 1, tzinfo=timezone.utc)
    )
    upsert_game(nba_game("future", "2026-10-26T23:00:00Z", status="FINAL",
                         home_score=113, away_score=105))
    result = settle_game("future")
    assert result["settled"] == 2
    comparison = shadow_comparison(season="2026-27")
    assert comparison["pairedGames"] == 1
    assert set(comparison["metricsByModel"]) == {
        NBA_GAME_MODEL_VERSION, NBA_GAME_RESEARCH_CANDIDATE_VERSION,
    }
    assert all(metrics["settled"] == 1 for metrics in comparison["metricsByModel"].values())
    summary = performance(season="2026-27")
    assert summary["modelVersion"] == NBA_GAME_MODEL_VERSION
    assert summary["predictions"] == 1
    assert summary["shadowComparison"]["pairedGames"] == 1
