import json
from pathlib import Path
import pytest
from backtesting.audit_nfl_player_prop_odds import audit_cache
from backtesting.markets import normalize_player_prop_market
from backtesting.player_prop_acquisition import plan_acquisition
from backtesting.player_prop_odds import (availability, execution_and_consensus, filter_player_quotes,
    grade_quote, normalize_provider_outcomes, pair_quotes, reconcile_player, simulation_fair_sgp_price)

PLAYERS=[{"game_id":"g1","player_id":"p1","player_name":"Pat Passer","team":"BUF"}]
def event(lines=(250.5,250.5)):
    return {"id":"e1","bookmakers":[{"key":"book-a","markets":[{"key":"player_pass_yds","last_update":"2025-09-01T10:00:00Z","outcomes":[
        {"name":"Over","description":"Pat Passer","point":lines[0],"price":-110}, {"name":"Under","description":"Pat Passer","point":lines[1],"price":-110}]}]}]}
def quotes(ev=None): return normalize_provider_outcomes(ev or event(),league="nfl",season=2025,week=1,game_id="g1",canonical_players=PLAYERS,snapshot_timestamp="2025-09-01T11:00:00Z")[0]

def test_aliases_and_unsupported():
    assert normalize_player_prop_market("player_reception_yds") == "receiving_yards"
    assert normalize_player_prop_market("player_anytime_td") is None
def test_reconciliation_failures():
    assert reconcile_player({"description":"Nobody"},PLAYERS,game_id="g1").status == "unknown_player"
    assert reconcile_player({"description":"Pat Passer","team":"MIA"},PLAYERS,game_id="g1").status == "team_mismatch"
    assert reconcile_player({"description":"Pat Passer"},PLAYERS*2,game_id="g1").status == "ambiguous_player"
def test_pair_no_vig_and_different_lines():
    assert pair_quotes(quotes())[0]["no_vig_over"] == pytest.approx(.5)
    assert len(pair_quotes(quotes(event((249.5,251.5))))) == 2
def test_best_execution_and_exact_line_consensus():
    rows=quotes(); better=dict(rows[0],bookmaker="book-b",american_odds=100,decimal_odds=2,implied_probability=.5)
    groups=execution_and_consensus(rows+[better]); assert groups[0]["best_over"]["bookmaker"] == "book-b"
def test_shared_cutoff_rejects_future_and_invalid():
    game={"game_id":"g1","prediction_cutoff":"2025-09-01T10:30:00Z","kickoff_time":"2025-09-01T12:00:00Z"}
    valid,diag=filter_player_quotes(game,quotes()); assert not valid and diag["rejected_future"] == 2
    bad=[dict(quotes()[0],market_last_update="bad")]; assert filter_player_quotes(game,bad)[1]["rejected_unknown_timestamp"] == 1
def test_grading_push_and_identity():
    q=dict(quotes()[0],line=250); result=grade_quote(q,[{"game_id":"g1","player_id":"p1","passing_yards":250}])
    assert result["result"] == "push" and result["under_result"] == "push"
def test_offline_audit_partial_and_plan(tmp_path):
    d=tmp_path/"nfl/2025/week_01"; d.mkdir(parents=True); (d/"odds_player_props.json").write_text(json.dumps(quotes()))
    report=audit_cache(tmp_path,season=2025,start_week=1,end_week=2)
    assert report["network_contacted"] is False and report["coverage"]["passing_yards"]["HISTORICAL_PRICE_READY"] == "PARTIAL"
    plan=plan_acquisition([{"game_id":"g1","provider_event_id":"e1"}],tmp_path)
    assert plan["network_contacted"] is False and plan["requests_required"] == 1 and plan["estimated_credits"] == 60
def test_audit_identity_does_not_count_null_as_a_player(tmp_path):
    d=tmp_path/"nfl/2025/week_01"; d.mkdir(parents=True)
    rows=quotes()+[{**quotes()[0],"canonical_player_id":None,"provider_player_name":"Fallback Person"}]
    (d/"player_prop_odds.json").write_text(json.dumps(rows))
    report=audit_cache(tmp_path,season=2025,start_week=1,end_week=1)
    assert report["unique_canonical_player_ids"]==1
    assert report["players"]==2 and report["quotes_missing_canonical_player_id"]==1
    assert report["quotes_using_fallback_identity"]==1 and report["invariants_passed"]

def test_raw_event_object_market_discovery(tmp_path):
    d=tmp_path/"raw_cache/nfl/2025/week_01"; d.mkdir(parents=True)
    raw=event(); raw["bookmakers"][0]["markets"].append({"key":"player_pass_tds","outcomes":[
        {"name":"Over","description":"Pat Passer","point":1.5,"price":110},
        {"name":"Under","description":"Pat Passer","point":1.5,"price":-130}]})
    (d/"response.json").write_text(json.dumps({"timestamp":"2025-09-01T11:00:00Z","data":raw}))
    report=audit_cache(tmp_path,season=2025,start_week=1,end_week=1)
    assert report["raw_provider_coverage"]=={"player_pass_tds":2,"player_pass_yds":2}
def test_missing_pricing_and_fair_sgp_label():
    assert availability([],requested_weeks=[1])["passing_yards"]["HISTORICAL_PRICE_READY"] == "NOT_READY"
    fair=simulation_fair_sgp_price(.25); assert fair["simulation_fair_decimal_odds"] == 4 and fair["sportsbook_ev"] is None and "MODEL_FAIR" in fair["price_type"]
