import json
import pytest

from backtesting.evaluation import american_profit, betting_metrics, confidence_buckets, edge_buckets, probability_metrics
from backtesting.compare_models import market_metrics, paired_decisions
from backtesting.nfl_v1_v2_validation import evaluate, render_report, validate_game


def test_probability_metrics_exclude_pushes_at_call_site_and_are_exact():
    metrics = probability_metrics([(.8, 1), (.2, 0)])
    assert metrics["accuracy"] == 1
    assert metrics["brier"] == pytest.approx(.04)
    assert metrics["count"] == 2


def test_roi_drawdown_losing_streak_and_push():
    rows = [
        {"bet": True, "grade": "loss", "odds_used": -110},
        {"bet": True, "grade": "loss", "odds_used": -110},
        {"bet": True, "grade": "push", "odds_used": -110},
        {"bet": True, "grade": "win", "odds_used": 200},
    ]
    result = betting_metrics(rows)
    assert result["profit_loss"] == 0
    assert result["roi"] == 0
    assert result["pushes"] == 1
    assert result["win_rate"] == 1 / 3
    assert result["max_drawdown"] == 2
    assert result["longest_losing_streak"] == 2
    assert result["units_won"] == 2
    assert result["units_lost"] == 2


def test_american_price_profit_is_exact_for_wins_losses_and_pushes():
    assert american_profit(200, "win") == 2
    assert american_profit(-125, "win") == pytest.approx(.8)
    assert american_profit(-110, "loss") == -1
    assert american_profit(150, "push") == 0


def test_edge_buckets_have_stable_boundaries():
    rows = [{"edge": edge, "model_probability": .5} for edge in (-.01, 0, .02, .10)]
    result = edge_buckets(rows)
    assert [row["predictions"] for row in result] == [1, 1, 1, 0, 0, 0, 1]


def test_market_probability_metrics_and_confidence_buckets_exclude_push():
    rows = [{"grade": grade, "sportsbook_odds": -110, "model_probability": probability,
             "execution_implied_probability": .5238, "consensus_probability": .5, "edge": probability-.5}
            for grade, probability in (("win", .6), ("loss", .7), ("push", .8))]
    metrics = market_metrics(rows)
    assert metrics["probability_quality"]["count"] == 2
    assert metrics["probability_quality"]["brier"] == pytest.approx((.16+.49)/2)
    assert sum(bucket["count"] for bucket in confidence_buckets(
        [{**r, "bet": True, "odds_used": -110} for r in rows])) == 3


def test_paired_decisions_include_all_five_categories():
    a = [{"game": "same", "market": "h2h", "selection": "A", "model_probability": .6, "edge": .1, "grade": "win"},
         {"game": "opp", "market": "h2h", "selection": "A", "model_probability": .6, "edge": .1, "grade": "loss"},
         {"game": "one", "market": "h2h", "selection": "A", "model_probability": .6, "edge": .1, "grade": "win"}]
    b = [{"game": "same", "market": "h2h", "selection": "A", "model_probability": .7, "edge": .2, "grade": "win"},
         {"game": "opp", "market": "h2h", "selection": "B", "model_probability": .7, "edge": .2, "grade": "win"},
         {"game": "two", "market": "h2h", "selection": "B", "model_probability": .7, "edge": .2, "grade": "loss"}]
    universe = {(x, "h2h") for x in ("same", "opp", "one", "two", "none")}
    counts = paired_decisions({"v1": a, "v2": b}, ["v1", "v2"], universe)["counts"]
    assert set(counts.values()) == {1}


def test_snapshot_rejects_post_kickoff_and_missing_data():
    game = {"game_id": "g", "kickoff_time": "2025-09-01T17:00:00Z", "home_team": "A", "away_team": "B"}
    odds = [{"game_id": "g", "captured_at": "2025-09-01T18:00:00Z"}]
    outcomes = [{"game_id": "g", "final_home_score": 1, "final_away_score": 0}]
    history = [{"team": team, "data_as_of": "2025-08-01T00:00:00Z"} for team in ("A", "B")]
    assert validate_game(game, odds, outcomes, history) == ["POST_KICKOFF_DATA"]
    assert "MISSING_TEAM_HISTORY" in validate_game(game, odds, outcomes, [])


def test_empty_discovery_is_deterministic_and_inconclusive(tmp_path):
    first = evaluate(tmp_path); second = evaluate(tmp_path)
    assert first == second
    assert first["universe"]["paired_game_ids"] == []
    assert first["universe"]["valid_v1_games"] == first["universe"]["valid_v2_games"] == 0
    assert first["conclusion"] == "INCONCLUSIVE — INSUFFICIENT DATA"
    assert render_report(first) == render_report(second)
    assert "## Season Breakdown" in render_report(first)


def test_exclusion_accounting_and_season_discovery(tmp_path):
    directory = tmp_path / "nfl" / "2024" / "week_02"; directory.mkdir(parents=True)
    game = {"game_id": "g", "kickoff_time": "2024-09-08T17:00:00Z", "home_team": "A", "away_team": "B"}
    (directory / "games.json").write_text(json.dumps([game]))
    for name in ("odds", "outcomes", "team_stats", "injuries", "weather"):
        (directory / f"{name}.json").write_text("[]")
    result = evaluate(tmp_path)
    assert result["coverage"]["periods"][0]["week"] == 2
    assert result["universe"]["total_discovered_games"] == 1
    assert result["universe"]["exclusion_reasons"] == {
        "MISSING_ODDS": 1, "MISSING_OUTCOME": 1, "MISSING_TEAM_HISTORY": 1}
