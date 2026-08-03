from __future__ import annotations

import json
from pathlib import Path

import pytest

from backtesting.forward_shadow_nfl_player_props import _digest
from backtesting.generate_nfl_system_a_shadow_candidates import (
    LAUNCH_MARKETS,
    freeze_prediction_config,
    prepare_quote_pairs,
    shadow_readiness,
)


def _games() -> list[dict]:
    return [{"season": 2026, "week": 1, "game_id": "g1", "home_team": "BUF", "away_team": "MIA",
             "prediction_cutoff": "2026-09-01T11:00:00Z", "kickoff_time": "2026-09-01T12:00:00Z"}]


def _quotes() -> list[dict]:
    rows = []
    for book, over, under in (("a", 2.0, 1.8), ("b", 1.9, 2.1)):
        for side, decimal in (("OVER", over), ("UNDER", under)):
            rows.append({"season": 2026, "week": 1, "game_id": "g1", "canonical_player_id": "p1",
                "player_name": "Player", "team": "BUF", "market": "receptions", "line": 4.5,
                "side": side, "bookmaker": book, "decimal_odds": decimal,
                "quote_timestamp": "2026-09-01T10:00:00Z"})
    return rows


def test_quote_pair_keeps_consensus_separate_from_best_execution_price() -> None:
    pair = prepare_quote_pairs(_quotes(), _games(), generated_at="2026-09-01T10:05:00Z")[0]
    assert pair["complete_books"] == 2
    assert pair["best_over"]["bookmaker"] == "a"
    assert pair["best_under"]["bookmaker"] == "b"
    assert pair["consensus_no_vig_over"] + pair["consensus_no_vig_under"] == pytest.approx(1.0)


def test_quote_pair_fails_closed_on_late_or_outcome_contaminated_input() -> None:
    late = [{**row, "quote_timestamp": "2026-09-01T11:00:01Z"} for row in _quotes()]
    with pytest.raises(ValueError, match="violates prediction cutoff"):
        prepare_quote_pairs(late, _games(), generated_at="2026-09-01T10:05:00Z")
    contaminated = [{**row, "grade": "WIN"} for row in _quotes()]
    with pytest.raises(ValueError, match="outcome-bearing"):
        prepare_quote_pairs(contaminated, _games(), generated_at="2026-09-01T10:05:00Z")
    completed = [{**_games()[0], "status": "completed"}]
    with pytest.raises(ValueError, match="completed outcome state"):
        prepare_quote_pairs(_quotes(), completed, generated_at="2026-09-01T10:05:00Z")


def test_incomplete_book_pair_is_not_used_for_consensus() -> None:
    quotes = [row for row in _quotes() if row["bookmaker"] == "a" or row["side"] == "OVER"]
    pair = prepare_quote_pairs(quotes, _games(), generated_at="2026-09-01T10:05:00Z")[0]
    assert pair["complete_books"] == 1


def test_quote_pair_rejects_conflicting_side_teams() -> None:
    quotes = _quotes(); quotes[0]["team"] = "MIA"
    with pytest.raises(ValueError, match="conflicting teams"):
        prepare_quote_pairs(quotes, _games(), generated_at="2026-09-01T10:05:00Z")


def test_freeze_prediction_config_uses_final_preoutcome_distribution_folds(tmp_path: Path) -> None:
    experiment = tmp_path / "experiment.json"
    folds = []
    for market in LAUNCH_MARKETS:
        folds.extend([
            {"market": market, "status": "COMPLETE", "test_season": 2025, "test_week": 17,
             "selected_configuration": "old", "selection": {"prior_rows": 1}},
            {"market": market, "status": "COMPLETE", "test_season": 2025, "test_week": 18,
             "selected_configuration": f"final_{market}", "selection": {"prior_rows": 2}},
        ])
    experiment.write_text(json.dumps({"configuration": {"seed": 7, "seasons": [2023, 2024, 2025],
        "min_history": 2}, "folds": folds}), encoding="utf-8")
    calibration = []
    for market in LAUNCH_MARKETS:
        for side in ("OVER", "UNDER"):
            for week in range(1, 7):
                for index in range(50):
                    probability = .55 + (index % 10) * .03
                    calibration.append({"season": 2025, "week": week, "market": market, "side": side,
                        "raw_probability": probability, "result": "WIN" if (index + week) % 2 else "LOSS"})
    calibration_path = tmp_path / "calibration.json"; calibration_path.write_text(json.dumps(calibration), encoding="utf-8")
    history = tmp_path / "history.json"; history.write_text("[]", encoding="utf-8")
    policy = {"configuration": "residual_half_life_9", "minimum_expected_value": .05,
              "policy_fingerprint": "f" * 64}
    policy_path = tmp_path / "policy.json"; policy_path.write_text(json.dumps(policy), encoding="utf-8")
    output = tmp_path / "config.json"
    config = freeze_prediction_config(experiment_path=experiment, calibration_rows_path=calibration_path,
        price_history_path=history, shadow_policy_path=policy_path, output_path=output)
    assert config["distribution_configuration_by_market"]["receptions"] == "final_receptions"
    fingerprint = config.pop("configuration_fingerprint")
    assert fingerprint == _digest(config)


def test_readiness_waits_for_both_games_and_quotes(tmp_path: Path) -> None:
    week = tmp_path / "nfl" / "2026" / "week_01"; week.mkdir(parents=True)
    (week / "games.json").write_text("[]", encoding="utf-8")
    waiting = shadow_readiness(tmp_path, season=2026)
    assert waiting["status"] == "WAITING_FOR_PREGAME_GAMES_AND_QUOTES"
    (week / "player_prop_odds.json").write_text("[]", encoding="utf-8")
    assert shadow_readiness(tmp_path, season=2026)["status"] == "READY_FOR_CANDIDATE_GENERATION"
