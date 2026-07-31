import json

import pytest

from backtesting.evaluate_nfl_player_props import (
    american_implied_probability, edge_bucket, evaluate, flat_profit,
    no_vig_probabilities, probability_metrics, select_best_prices, write_outputs,
)


def test_pricing_returns_and_edge_boundaries():
    assert american_implied_probability(100) == pytest.approx(.5)
    assert american_implied_probability(-150) == pytest.approx(.6)
    over, under = no_vig_probabilities(-110, -110)
    assert (over, under) == pytest.approx((.5, .5))
    assert flat_profit(120, "WIN") == pytest.approx(1.2)
    assert flat_profit(-125, "WIN") == pytest.approx(.8)
    assert flat_profit(-110, "LOSS") == -1
    assert flat_profit(-110, "PUSH") == 0
    assert [edge_bucket(x) for x in (0, .0001, .02, .05, .10)] == [
        "edge <= 0", "0-2%", "2-5%", "5-10%", ">=10%"]


def test_probability_metrics_clip_log_loss_and_exclude_pushes():
    rows=[{"grade":"WIN","p":1.0},{"grade":"LOSS","p":0.0},{"grade":"PUSH","p":.8}]
    metric=probability_metrics(rows,"p")
    assert metric["count"] == 2
    assert metric["pushes_excluded"] == 1
    assert metric["brier_score"] == pytest.approx(1e-12)
    assert metric["log_loss"] == pytest.approx(-__import__("math").log(1-1e-6))


def test_best_price_collapses_quotes_and_ignores_outcome():
    base={"season":2025,"week":1,"game_id":"g","canonical_player_id":"p","market":"receptions",
          "side":"OVER","line":4.5,"quote_timestamp":"2025-09-01T00:00:00Z","decimal_odds":1.91}
    rows=[{**base,"bookmaker":"a","grade":"WIN"},{**base,"bookmaker":"b","decimal_odds":2.1,"grade":"LOSS"}]
    assert select_best_prices(rows)[0]["bookmaker"] == "b"
    swapped=[{**r,"grade":"LOSS" if r["grade"]=="WIN" else "WIN"} for r in rows]
    assert select_best_prices(swapped)[0]["bookmaker"] == "b"


def _snapshot(tmp_path, *, post_cutoff=False, incomplete=False):
    directory=tmp_path/"nfl"/"2025"/"week_01"; directory.mkdir(parents=True)
    game={"game_id":"g","season":2025,"week":1,"kickoff_time":"2025-09-05T00:00:00Z",
          "prediction_cutoff":"2025-09-04T23:00:00Z","home_team":"KC","away_team":"PHI"}
    (directory/"games.json").write_text(json.dumps([game]))
    stamp="2025-09-05T00:01:00Z" if post_cutoff else "2025-09-04T22:00:00Z"
    common={"season":2025,"week":1,"game_id":"g","canonical_player_id":"p1","player_name":"Player",
            "team":"KC","market":"receiving_yards","line":50.5,"bookmaker":"book","snapshot_timestamp":stamp}
    quotes=[{**common,"selection":"OVER","american_odds":110,"model_probability":.6}]
    if not incomplete: quotes.append({**common,"selection":"UNDER","american_odds":-130,"model_probability":.4})
    (directory/"player_prop_odds.json").write_text(json.dumps(quotes))
    outcome={"game_id":"g","canonical_player_id":"p1","record_role":"game_outcome","is_pregame":False,
             "stats":{"receiving_yards":60}}
    (directory/"player_stats.json").write_text(json.dumps([outcome]))
    return directory


def test_offline_evaluation_pairing_roi_and_determinism(tmp_path, monkeypatch):
    _snapshot(tmp_path)
    # Any accidental provider path fails the test; evaluation needs no network module.
    monkeypatch.setattr("socket.create_connection",lambda *a,**k: (_ for _ in ()).throw(AssertionError("network")))
    one=evaluate(tmp_path,2025,1,1); two=evaluate(tmp_path,2025,1,1)
    assert json.dumps(one,sort_keys=True) == json.dumps(two,sort_keys=True)
    assert one["summary"]["gradeable_quotes"] == 2
    assert one["summary"]["unique_opportunities"] == 2
    assert one["opportunity_rows"][0]["edge"] is not None
    positive=[r for r in one["opportunity_rows"] if r["edge"]>0]
    assert positive[0]["profit_units"] == pytest.approx(1.1)
    out=tmp_path/"out"; write_outputs(one,out)
    assert (out/"evaluation_summary.json").exists()
    before=(out/"manifest.json").read_text(); write_outputs(two,out)
    assert (out/"manifest.json").read_text() == before


def test_incomplete_and_mismatched_lines_are_not_paired(tmp_path):
    directory=_snapshot(tmp_path,incomplete=True)
    quotes=json.loads((directory/"player_prop_odds.json").read_text())
    quotes.append({**quotes[0],"selection":"UNDER","line":51.5,"american_odds":-130,"model_probability":.4})
    (directory/"player_prop_odds.json").write_text(json.dumps(quotes))
    report=evaluate(tmp_path,2025,1,1)
    assert report["summary"]["incomplete_pair_quotes"] == 2
    assert all(row["no_vig_market_probability"] is None for row in report["opportunity_rows"])


def test_post_cutoff_quote_fails_integrity_validation(tmp_path):
    _snapshot(tmp_path,post_cutoff=True)
    with pytest.raises(ValueError,match="integrity validation failed"):
        evaluate(tmp_path,2025,1,1)
