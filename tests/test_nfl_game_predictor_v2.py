from __future__ import annotations

import pytest

from backtesting.metrics import MetricsCalculator
from backtesting.nfl_game_predictor import (
    NFLGameMarketPredictor,
    NFLGameMarketPredictorV2,
    NFLGameModelConfig,
    V1_MODEL_VERSION,
    V2_MODEL_VERSION,
    no_vig_probabilities,
    sportsbook_consensus,
)


def row(team, opponent, week, points, allowed, venue="home", *, stamp=None):
    stamp = stamp or f"2024-{9 + week // 4:02d}-{7 + week % 4:02d}T23:00:00Z"
    return {"team": team, "opponent": opponent, "season": 2024, "week": week,
            "game_id": f"g-{week}-{min(team, opponent)}-{max(team, opponent)}",
            "points_for": points, "points_against": allowed, "home_away": venue,
            "completed_at": stamp, "data_as_of": stamp,
            "record_role": "completed_game_history", "is_pregame": False}


GAME = {"game_id": "target", "season": 2025, "week": 1, "home_team": "BUF",
        "away_team": "MIA", "kickoff_time": "2025-09-07T17:00:00Z"}


def history():
    rows = []
    for week, (buf, mia) in enumerate(((17, 30), (20, 27), (24, 21), (31, 17), (35, 14), (28, 20)), 1):
        rows += [row("BUF", "MIA", week, buf, mia, "home" if week % 2 else "away"),
                 row("MIA", "BUF", week, mia, buf, "away" if week % 2 else "home")]
    return rows


def test_v1_formula_is_frozen_before_v2_selection():
    projection = NFLGameMarketPredictor(V1_MODEL_VERSION).project(GAME, history())
    assert projection.model_version == V1_MODEL_VERSION
    assert projection.home_points == pytest.approx((25.833333 + 25.833333) / 2 + .75, abs=1e-5)
    assert projection.expected_margin == pytest.approx(5.833333)


def test_recency_weighting_and_split_shrinkage_are_deterministic():
    predictor = NFLGameMarketPredictorV2(NFLGameModelConfig(decay=.5, split_prior_games=2))
    assert predictor.weighted_average([10, 20, 30]) == pytest.approx(24.285714)
    projection = predictor.project(GAME, history())
    assert projection.features["home_venue_points_for"] != projection.features["home_weighted_points_for"]
    assert projection == predictor.project(GAME, history())


def test_elo_update_regression_and_future_leakage():
    predictor = NFLGameMarketPredictorV2(NFLGameModelConfig(margin_of_victory=False))
    winner, loser = predictor.elo_update(1500, 1500, 24, 17)
    assert winner > 1500 > loser
    assert predictor.regress_elo(1600) == pytest.approx(1567)
    before = predictor.project(GAME, history())
    future = row("BUF", "MIA", 7, 99, 0, stamp="2025-09-08T23:00:00Z")
    assert predictor.project(GAME, history() + [future]) == before
    diagnostics = predictor.last_diagnostics
    assert diagnostics["BUF"]["history_rows_loaded"] == 7
    assert diagnostics["BUF"]["history_rows_used"] == 6
    assert diagnostics["BUF"]["rejected_future_rows"] == 1
    assert diagnostics["BUF"]["latest_history_timestamp"] < GAME["kickoff_time"]
    assert diagnostics["BUF"]["seasons_used"] == [2024]


def test_opponent_adjustment_rest_and_score_distribution():
    projection = NFLGameMarketPredictor(V2_MODEL_VERSION).project(GAME, history())
    assert projection.features["home_offensive_strength"] != 0
    assert projection.features["home_days_since_last_game"] > 100
    home = projection.probability("h2h", "BUF", home_team="BUF", away_team="MIA")
    away = projection.probability("h2h", "MIA", home_team="BUF", away_team="MIA")
    assert 0 < home < 1 and home + away == pytest.approx(1)
    assert projection.probability("total", "over", 45, home_team="BUF", away_team="MIA") + projection.probability("total", "under", 45, home_team="BUF", away_team="MIA") == pytest.approx(1)
    output = projection.output(home_team="BUF", away_team="MIA", spread=-2.5, total=45,
                               market_probability=.5, market_weight=.2)
    assert output["blended_probability"] == pytest.approx(.8*output["raw_model_probability"] + .1)
    assert output["home_cover_probability"] + output["away_cover_probability"] == pytest.approx(1)


def test_no_vig_and_sportsbook_consensus_separate_execution():
    probabilities = no_vig_probabilities([-110, -110])
    assert probabilities == pytest.approx([.5, .5]) and sum(probabilities) == pytest.approx(1)
    consensus = sportsbook_consensus([
        {"sportsbook": "A", "line": -2.5, "odds": -110},
        {"sportsbook": "B", "line": -3, "odds": 105},
        {"sportsbook": "C", "line": -3.5, "odds": -105},
    ], "spread")
    assert consensus["consensus_line"] == -3
    assert consensus["best_execution_quote"]["sportsbook"] == "B"


def test_probability_metrics_exclude_pushes():
    metrics = MetricsCalculator().calculate([
        {"grade": "win", "correct": True, "model_probability": .8},
        {"grade": "loss", "correct": False, "model_probability": .6},
        {"grade": "push", "model_probability": .99},
    ])
    assert metrics["brier_score"] == pytest.approx(.2)
    assert metrics["log_loss"] is not None
    assert sum(bucket["count"] for bucket in metrics["probability_calibration"].values()) == 2
