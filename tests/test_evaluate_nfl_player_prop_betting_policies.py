from backtesting.evaluate_nfl_player_prop_betting_policies import evaluate_policies


def _rows():
    rows = []
    for week, game, over_grade in ((1, "g1", "WIN"), (2, "g2", "LOSS"), (3, "g3", "WIN")):
        for side in ("OVER", "UNDER"):
            grade = over_grade if side == "OVER" else ("LOSS" if over_grade == "WIN" else "WIN")
            rows.append({"season": 2025, "week": week, "game_id": game,
                "canonical_player_id": "p", "market": "receptions", "line": 4.5,
                "side": side, "bookmaker": "book", "grade": grade,
                "profit_units": 1.0 if grade == "WIN" else -1.0, "decimal_odds": 2.0,
                "push_probability": 0.0,
                "no_vig_market_probability": 0.55 if side == "OVER" else 0.45,
                "baseline_probability": 0.6 if side == "OVER" else 0.4,
                "model_probability": 0.7 if side == "OVER" else 0.3})
    return rows


def test_every_policy_selects_at_most_one_side_and_controls_are_present():
    report = evaluate_policies(_rows(), decision_threshold=0.05, thresholds=(0.0, 0.05),
                               min_train_rows=100, draws=20, seed=7)
    assert report["base_opportunities"] == 3
    assert report["side_rows"] == 6
    assert report["one_side_per_base_contract"] is True
    assert report["evaluation_role"] == "HISTORICAL_VALIDATION_ONLY"
    assert report["evaluation_seasons"] == [2025]
    assert any("2025 has already been observed" in value for value in report["guardrails"])
    assert report["controls"]["always_over"]["bets"] == 3
    assert report["controls"]["always_under"]["bets"] == 3
    assert report["controls"]["no_bet"]["bets"] == 0
    assert set(report["probability_diagnostics"]) == {
        "market_no_vig", "v3_baseline", "v4_candidate", "market_residual_walk_forward"}
    assert all(0 <= value["ranking_auc"] <= 1 for value in report["probability_diagnostics"].values())
    assert report["selected_policy_metrics"]["v4_ev"]["bets"] <= 3
    assert report["selected_policy_metrics"]["market_residual_walk_forward_ev"]["bets"] <= 3
    comparison = report["policy_comparisons"]["market_residual_walk_forward_ev_vs_v3_ev"]
    assert "roi_improvement_gate_pass" in comparison
    assert "absolute_positive_roi_gate_pass" in comparison


def test_residual_model_falls_back_to_market_without_prior_history():
    report = evaluate_policies(_rows(), thresholds=(0.0,), min_train_rows=100, draws=0)
    assert all(fold["status"] == "MARKET_ONLY_INSUFFICIENT_PRIOR_HISTORY"
               for fold in report["residual_model_folds"])
    market = report["selected_policy_metrics"]["market_only_ev"]
    residual = report["selected_policy_metrics"]["market_residual_walk_forward_ev"]
    assert market == residual
