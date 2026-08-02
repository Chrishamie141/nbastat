from copy import deepcopy

from backtesting.evaluate_nfl_player_prop_betting_policies import (
    add_walk_forward_residual_probabilities,
)
from backtesting.evaluate_nfl_player_prop_nested_policy import evaluate_nested_policy


def _rows():
    rows = []
    periods = ((2024, 1), (2024, 2), (2024, 3), (2025, 1), (2025, 2))
    for period_index, (season, week) in enumerate(periods):
        for base in range(4):
            over_wins = (period_index + base) % 3 != 0
            for side in ("OVER", "UNDER"):
                win = over_wins if side == "OVER" else not over_wins
                grade = "WIN" if win else "LOSS"
                rows.append({"season": season, "week": week,
                    "game_id": f"g-{season}-{week}-{base}",
                    "canonical_player_id": f"p-{base}", "market": "receptions",
                    "line": 4.5 + base, "side": side, "bookmaker": "book",
                    "grade": grade, "profit_units": 0.91 if win else -1.0,
                    "decimal_odds": 1.91, "push_probability": 0.0,
                    "no_vig_market_probability": 0.55 if side == "OVER" else 0.45,
                    "baseline_probability": 0.60 if side == "OVER" else 0.40,
                    "model_probability": 0.65 if side == "OVER" else 0.35})
    return rows


def _evaluate(rows):
    return evaluate_nested_policy(rows, thresholds=(0.0, 0.05, 0.10),
        recency_half_lives=(None, 2.0), min_model_train_rows=4,
        min_prior_periods=1, inner_lookback_periods=2, min_inner_rows=8,
        min_inner_bets=1, min_inner_games=1, roi_shrinkage_bets=2,
        baseline_threshold=0.05, draws=20, seed=7)


def test_walk_forward_periods_do_not_mix_future_weeks_across_seasons():
    _enriched, folds = add_walk_forward_residual_probabilities(
        _rows(), min_train_rows=4, min_prior_weeks=1, seed=7)
    fold = next(value for value in folds
                if value["market"] == "receptions" and
                value["test_season"] == 2025 and value["test_week"] == 1)
    assert fold["train_periods"] == [[2024, 1], [2024, 2], [2024, 3]]
    assert [2025, 2] not in fold["train_periods"]


def test_nested_choice_for_final_fold_is_invariant_to_final_fold_outcomes():
    report, _bets = _evaluate(_rows())
    changed = deepcopy(_rows())
    for row in changed:
        if (row["season"], row["week"]) == (2025, 2):
            row["grade"] = "LOSS" if row["grade"] == "WIN" else "WIN"
            row["profit_units"] = 0.91 if row["grade"] == "WIN" else -1.0
    changed_report, _changed_bets = _evaluate(changed)
    final = next(value for value in report["folds"]
                 if (value["test_season"], value["test_week"]) == (2025, 2))
    changed_final = next(value for value in changed_report["folds"]
                         if (value["test_season"], value["test_week"]) == (2025, 2))
    assert final["selected_configuration"] == changed_final["selected_configuration"]
    assert final["selected_threshold"] == changed_final["selected_threshold"]
    assert final["inner_periods"] == changed_final["inner_periods"]


def test_nested_policy_never_selects_both_sides_of_a_base():
    report, bets = _evaluate(_rows())
    keys = {(row["season"], row["week"], row["game_id"],
             row["canonical_player_id"], row["market"], row["line"])
            for row in bets}
    assert len(keys) == len(bets)
    assert report["one_side_per_base_contract"] is True
    assert report["duplicate_base_bets"] == 0
    assert report["evaluation_role"] == "NESTED_HISTORICAL_VALIDATION_ONLY"
