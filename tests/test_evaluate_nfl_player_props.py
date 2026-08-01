import json

import pytest

from backtesting.evaluate_nfl_player_props import (
    american_implied_probability, edge_bucket, evaluate, flat_profit,
    no_vig_probabilities, probability_metrics, select_best_prices, write_outputs,
)
from backtesting.player_identity_registry import reconcile_outcome_identities
from backtesting.player_prop_odds import aggregate_player_outcomes, grade_quote


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
    quotes=[{**common,"selection":"OVER","american_odds":110}]
    if not incomplete: quotes.append({**common,"selection":"UNDER","american_odds":-130})
    (directory/"player_prop_odds.json").write_text(json.dumps(quotes))
    predictions=[{**common,"side":"OVER","model_probability":.6,"readiness":"READY"}]
    if not incomplete: predictions.append({**common,"side":"UNDER","model_probability":.4,"readiness":"READY"})
    (directory/"player_prop_predictions.json").write_text(json.dumps(predictions))
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
    quotes.append({**quotes[0],"selection":"UNDER","line":51.5,"american_odds":-130})
    (directory/"player_prop_odds.json").write_text(json.dumps(quotes))
    report=evaluate(tmp_path,2025,1,1)
    assert report["summary"]["incomplete_pair_quotes"] == 2
    assert all(row["no_vig_market_probability"] is None for row in report["opportunity_rows"])


def test_post_cutoff_quote_fails_integrity_validation(tmp_path):
    _snapshot(tmp_path,post_cutoff=True)
    with pytest.raises(ValueError,match="integrity validation failed"):
        evaluate(tmp_path,2025,1,1)


def test_evaluator_aggregates_production_shaped_player_stat_rows(tmp_path):
    directory=_snapshot(tmp_path)
    rows=[
        {"game_id":"g","canonical_player_id":"p1","player_name":"Player","team":"KC",
         "season":2025,"week":1,"record_role":"game_outcome","is_pregame":False,
         "category":"passing","stats":{"passing_yards":101,"passing_tds":1}},
        {"game_id":"g","canonical_player_id":"p1","player_name":"Player","team":"KC",
         "season":2025,"week":1,"record_role":"game_outcome","is_pregame":False,
         "category":"rushing","stats":{"rushing_attempts":2,"rushing_yards":8}},
        {"game_id":"g","canonical_player_id":"p1","player_name":"Player","team":"KC",
         "season":2025,"week":1,"record_role":"game_outcome","is_pregame":False,
         "category":"receiving","stats":{"receptions":4,"receiving_yards":60}},
    ]
    (directory/"player_stats.json").write_text(json.dumps(rows))
    one=evaluate(tmp_path,2025,1,1); two=evaluate(tmp_path,2025,1,1)
    assert one == two
    assert one["summary"]["gradeable_quotes"] == 2
    diagnostics=one["summary"]["outcome_aggregation"]
    assert diagnostics["raw_outcome_rows"] == 3
    assert diagnostics["already_canonical"] == 3
    assert diagnostics["canonical_player_outcomes"] == 1
    assert diagnostics["players_with_multiple_category_rows"] == 1
    assert {row["outcome"] for row in one["quote_rows"]} == {60}


def test_evaluator_joins_numeric_espn_athlete_to_string_quote_and_prediction(tmp_path):
    directory = _snapshot(tmp_path)
    player_id = "123456"
    for filename in ("player_prop_odds.json", "player_prop_predictions.json"):
        rows = json.loads((directory / filename).read_text())
        for row in rows:
            row["canonical_player_id"] = player_id
        (directory / filename).write_text(json.dumps(rows))
    outcomes = [
        {"game_id": "g", "athlete_id": 123456, "category": "receiving",
         "record_role": "game_outcome", "is_pregame": False,
         "stats": {"receptions": 4}},
        {"game_id": "g", "athlete_id": 123456, "category": "receiving_yards",
         "record_role": "game_outcome", "is_pregame": False,
         "stats": {"receiving_yards": 60}},
    ]
    (directory / "player_stats.json").write_text(json.dumps(outcomes))
    report = evaluate(tmp_path, 2025, 1, 1)
    assert report["summary"]["outcome_aggregation"]["canonical_player_outcomes"] == 1
    assert report["summary"]["gradeable_quotes"] == 2
    assert report["summary"]["unique_opportunities"] == 2
    assert {row["canonical_player_id"] for row in report["quote_rows"]} == {player_id}


def test_outcome_registry_reconciliation_precedence_provenance_and_aggregation():
    identities=[
        {"game_id":"espn-401772510","canonical_player_id":"2577417",
         "provider_player_id":"2577417","player_id":"history-alias",
         "player_name":"Exact Player","normalized_player_name":"exact player","team":"KC",
         "source":"provider_box_score","identity_provenance":["provider_box_score"]},
        {"game_id":"other-game","canonical_player_id":"other","provider_player_id":"2577417",
         "player_id":"other","player_name":"Exact Player","team":"KC"},
    ]
    rows=[
        {"game_id":"espn-401772510","athlete_id":2577417,"player_name":"Exact Player",
         "team":"KC","category":"passing","stats":{"passing_yards":301,"passing_tds":3}},
        {"game_id":"espn-401772510","player_id":"history-alias","player_name":"Exact Player",
         "team":"KC","category":"rushing","stats":{"rushing_yards":12}},
    ]
    reconciled, diagnostics=reconcile_outcome_identities(rows,identities)
    assert diagnostics["reconciled_by_provider_id"] == 1
    assert diagnostics["reconciled_by_alias"] == 1
    assert all(row["canonical_player_id"] == "2577417" for row in reconciled)
    assert reconciled[0]["original_athlete_id"] == 2577417
    assert reconciled[0]["identity_provenance"] == ["provider_box_score"]
    outcomes, aggregation=aggregate_player_outcomes(reconciled)
    assert aggregation["canonical_player_outcomes"] == 1
    assert outcomes[("espn-401772510","2577417")]["stats"] == {
        "passing_tds":3,"passing_yards":301,"rushing_yards":12}
    for market,line in (("passing_tds",2.5),("passing_yards",299.5)):
        graded=grade_quote({"game_id":"espn-401772510","canonical_player_id":"2577417",
                            "market":market,"line":line,"selection":"OVER"},outcomes)
        assert graded["result"] == "win"


def test_fallback_canonical_maps_from_provider_without_name_fallback():
    identities=[{"game_id":"g","canonical_player_id":"history:g:KC:player",
                 "provider_player_id":"espn-7","player_id":"history:g:KC:player",
                 "player_name":"Player","team":"KC","source":"historical_roster"}]
    rows, diagnostics=reconcile_outcome_identities(
        [{"game_id":"g","provider_player_id":"espn-7","stats":{"passing_tds":1}}],identities)
    assert rows[0]["canonical_player_id"] == "history:g:KC:player"
    assert rows[0]["reconciliation_method"] == "reconciled_by_provider_id"
    assert diagnostics["reconciled_by_exact_name_team_game"] == 0


def test_exact_name_team_is_conservative_and_ambiguity_fails_closed():
    identities=[
        {"game_id":"g","canonical_player_id":"a","player_name":"Same Name","team":"KC"},
        {"game_id":"g","canonical_player_id":"b","player_name":"Same Name","team":"KC"},
        {"game_id":"g","canonical_player_id":"c","player_name":"Unique Name","team":"PHI"},
    ]
    rows, diagnostics=reconcile_outcome_identities([
        {"game_id":"g","player_name":"Unique Name","team":"PHI","stats":{"receptions":2}},
        {"game_id":"g","player_name":"Same Name","team":"KC","stats":{"receptions":3}},
        {"game_id":"g","player_name":"Unique","team":"PHI","stats":{"receptions":4}},
        {"game_id":"g","player_name":"Unique Name","team":"KC","stats":{"receptions":5}},
    ],identities)
    assert [row["canonical_player_id"] for row in rows] == ["c"]
    assert diagnostics["reconciled_by_exact_name_team_game"] == 1
    assert diagnostics["ambiguous"] == 1 and diagnostics["unresolved"] == 2
    assert diagnostics["ambiguities"][0]["candidate_canonical_ids"] == ["a","b"]


def test_evaluator_reconciles_week_registry_provider_id(tmp_path):
    directory=_snapshot(tmp_path)
    for filename in ("player_prop_odds.json","player_prop_predictions.json"):
        rows=json.loads((directory/filename).read_text())
        for row in rows: row["canonical_player_id"]="history:g:KC:player"
        (directory/filename).write_text(json.dumps(rows))
    (directory/"player_identities.json").write_text(json.dumps([{
        "game_id":"g","canonical_player_id":"history:g:KC:player",
        "provider_player_id":"2577417","player_id":"history:g:KC:player",
        "player_name":"Player","team":"KC","identity_provenance":["provider_box_score"]}]))
    (directory/"player_stats.json").write_text(json.dumps([{
        "game_id":"g","athlete_id":2577417,"record_role":"game_outcome","is_pregame":False,
        "stats":{"receiving_yards":60}}]))
    report=evaluate(tmp_path,2025,1,1)
    assert report["summary"]["gradeable_quotes"] == 2
    assert report["summary"]["outcome_aggregation"]["reconciled_by_provider_id"] == 1
