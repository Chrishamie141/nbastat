from __future__ import annotations

from datetime import datetime, timezone

from backend.app.services.nfl_audit_analytics import (
    canonicalize_prediction_rows, weekly_prediction_metrics,
)
from backend.app.services.nfl_model_policy import model_policy, winner_model_is_authorized
from backtesting.nfl_game_predictor import V2_MODEL_VERSION
from backtesting.nfl_v3 import V3_MODEL_VERSION


def prediction(game_id="espn-1", *, user_id=0, winner="BUF", probability=.62,
               status="WON", model=V2_MODEL_VERSION):
    return {
        "prediction_id": user_id + 1, "user_id": user_id, "season": 2026,
        "season_type": "regular", "week": 5, "game_id": game_id,
        "kickoff_time": "2026-10-04T17:00:00Z", "generated_at": "2026-10-03T12:00:00Z",
        "home_team": "BUF", "away_team": "MIA", "predicted_winner": winner,
        "model_probability": probability, "model_version": model,
        "frozen_prediction_hash": "same", "settlement_status": status,
    }


def observation(price=-200, *, at="2026-10-03T13:00:00Z", retrieved=None):
    return {
        "retrievedAt": retrieved or at, "marketTimestamp": at,
        "provider": "the-odds-api",
        "market": {"provider": "the-odds-api", "sportsbook": "draftkings",
                   "marketTimestamp": at, "homeOdds": price, "awayOdds": 170,
                   "coverage": {"moneyline": True}},
    }


def test_user_scoped_rows_are_canonicalized_without_double_counting():
    system = prediction(user_id=0)
    user = {**prediction(user_id=9), "prediction_id": 99}
    result = canonicalize_prediction_rows([user, system])

    assert result["canonicalRowCount"] == 1
    assert result["userScopedRowsCollapsed"] == 1
    assert result["rows"][0]["user_id"] == 0
    assert result["conflicts"] == []


def test_conflicting_user_scope_is_reported_not_silently_aggregated():
    system = prediction(user_id=0)
    user = {**prediction(user_id=9, winner="MIA"), "prediction_id": 99,
            "frozen_prediction_hash": "different"}
    result = canonicalize_prediction_rows([user, system])

    assert result["canonicalRowCount"] == 1
    assert result["conflicts"] == [{"gameId": "espn-1", "variants": 2}]


def test_weekly_metrics_use_only_strict_pregame_prices_for_roi_and_clv():
    row = prediction()
    first = observation(-200)
    closing = observation(-250, at="2026-10-04T16:59:59Z")
    report = weekly_prediction_metrics(
        rows=[row], market_histories={"espn-1": {
            "first": first, "latest": closing, "closing": closing,
            "operationalKickoff": row["kickoff_time"], "rejectedPostKickoffCount": 2,
        }}, now=datetime(2026, 10, 5, tzinfo=timezone.utc),
    )

    assert report["predictionPerformance"]["accuracy"] == 100
    assert report["bettingPerformance"]["netUnits"] == .5
    assert report["bettingPerformance"]["roi"] == 50
    assert report["bettingPerformance"]["clvSamples"] == 1
    assert report["bettingPerformance"]["averageClvProbabilityPoints"] > 0
    assert report["marketCapture"]["rejectedPostKickoffObservations"] == 2


def test_postkickoff_or_unattributed_prices_never_qualify():
    row = prediction()
    post = observation(-110, at="2026-10-04T17:00:00Z")
    post["market"]["sportsbook"] = None
    report = weekly_prediction_metrics(
        rows=[row], market_histories={"espn-1": {
            "first": post, "latest": post, "closing": post,
            "operationalKickoff": row["kickoff_time"],
        }}, now=datetime(2026, 10, 5, tzinfo=timezone.utc),
    )

    assert report["bettingPerformance"]["roi"] is None
    assert report["bettingPerformance"]["clvSamples"] == 0
    assert report["marketCapture"]["missingGameIds"] == ["espn-1"]


def test_model_policy_keeps_v2_champion_and_research_models_isolated():
    policy = model_policy()
    assert policy["winner"]["productionChampion"] == V2_MODEL_VERSION
    assert policy["winner"]["challenger"] == V3_MODEL_VERSION
    assert policy["winner"]["challengerStatus"] == "RESEARCH_ONLY"
    assert policy["playerProps"]["status"] == "RESEARCH_ONLY"
    assert policy["playerProps"]["promotionBlocked"] is True
    assert policy["parlays"]["status"] == "EXPERIMENTAL"
    assert policy["parlays"]["independenceProductMayBePublished"] is False
    assert winner_model_is_authorized(V2_MODEL_VERSION, season_type="regular")
    assert not winner_model_is_authorized(V3_MODEL_VERSION, season_type="regular")
