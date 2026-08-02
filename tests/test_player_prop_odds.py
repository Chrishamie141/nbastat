import json
from pathlib import Path
import pytest
from backtesting.audit_nfl_player_prop_odds import audit_cache
from backtesting.markets import normalize_player_prop_market
from backtesting.player_prop_acquisition import plan_acquisition
from backtesting.player_prop_odds import (availability, execution_and_consensus, filter_player_quotes,
    aggregate_player_outcomes, grade_quote, normalize_provider_outcomes, pair_quotes, reconcile_player,
    simulation_fair_sgp_price)
from backtesting.player_identity import normalize_player_id

PLAYERS=[{"game_id":"g1","player_id":"p1","player_name":"Pat Passer","team":"BUF"}]
def event(lines=(250.5,250.5)):
    return {"id":"e1","bookmakers":[{"key":"book-a","markets":[{"key":"player_pass_yds","last_update":"2025-09-01T10:00:00Z","outcomes":[
        {"name":"Over","description":"Pat Passer","point":lines[0],"price":-110}, {"name":"Under","description":"Pat Passer","point":lines[1],"price":-110}]}]}]}
def quotes(ev=None): return normalize_provider_outcomes(ev or event(),league="nfl",season=2025,week=1,game_id="g1",canonical_players=PLAYERS,snapshot_timestamp="2025-09-01T11:00:00Z")[0]

def test_aliases_and_unsupported():
    assert normalize_player_prop_market("player_reception_yds") == "receiving_yards"
    assert normalize_player_prop_market("player_pass_yds") == "passing_yards"
    assert normalize_player_prop_market("player_pass_tds") == "passing_tds"
    assert normalize_player_prop_market("player_anytime_td") is None
def test_reconciliation_failures():
    assert reconcile_player({"description":"Nobody"},PLAYERS,game_id="g1").status == "UNKNOWN"
    assert reconcile_player({"description":"Pat Passer","team":"MIA"},PLAYERS,game_id="g1").status == "UNKNOWN"
    assert reconcile_player({"description":"Pat Passer"},PLAYERS*2,game_id="g1").status == "EXACT_NAME_TEAM"

@pytest.mark.parametrize("value", [None, "", "  ", "None", "none", "null", "NULL"])
def test_null_player_id_spellings_are_missing(value):
    assert normalize_player_id(value) is None

def test_literal_null_id_uses_real_history_fallback_for_qb():
    players=[{"game_id":"g1","player_id":"None","player_name":"Week One QB","team":"DAL","position":"QB"}]
    rec=reconcile_player({"description":"Week One QB","player_id":"null","team":"DAL"},players,game_id="g1")
    assert rec.status == "EXACT_NAME_TEAM_GAME"
    assert rec.canonical_player_id == "history:g1:DAL:week one qb"
    rows,_=normalize_provider_outcomes(event={"id":"e1","bookmakers":[{"key":"b","markets":[
        {"key":"player_pass_yds","outcomes":[{"name":"Over","description":"Week One QB","team":"DAL","point":250.5,"price":-110}]}]}]},
        league="nfl",season=2025,week=1,game_id="g1",canonical_players=players,
        snapshot_timestamp="2025-09-01T00:00:00Z")
    assert rows[0]["canonical_player_id"].startswith("history:g1:DAL:")
    assert rows[0]["reconciliation_method"] == "EXACT_NAME_TEAM_GAME"

def test_current_game_identity_metadata_needs_no_feature_stats():
    roster_identity={"game_id":"g1","athlete_id":"12345","player_name":"Rookie QB","team":"PHI","position":"QB"}
    rec=reconcile_player({"description":"Rookie QB","team":"PHI"},[roster_identity],game_id="g1")
    assert rec.canonical_player_id == "12345"
    assert "stats" not in rec.player

def test_missing_ids_do_not_collapse_players_or_quote_identity():
    players=[{"game_id":"g1","player_name":f"Player {n}","team":"BUF"} for n in range(100)]
    resolved=[reconcile_player({"description":p["player_name"]},players,game_id="g1") for p in players]
    assert len({r.canonical_player_id for r in resolved}) == 100
    base={"league":"nfl","season":2025,"week":1,"game_id":"g1","canonical_player_id":None,
          "market":"receptions","bookmaker":"book","line":2.5,"selection":"OVER",
          "provider_snapshot_timestamp":"2025-09-01T00:00:00Z","market_last_update":"2025-09-01T00:00:00Z","american_odds":-110}
    from backtesting.player_prop_odds import deduplicate_quotes
    rows,diag=deduplicate_quotes([{**base,"provider_player_name":"Player A"},{**base,"provider_player_name":"Player B"}])
    assert len(rows)==2 and diag["duplicate_conflict"]==0

def test_provider_id_preference_and_team_ambiguity():
    players=[{"game_id":"g1","player_id":"p1","player_name":"Same Name","team":"BUF"},
             {"game_id":"g1","player_id":"p2","player_name":"Same Name","team":"MIA"}]
    assert reconcile_player({"player_id":"p2","description":"Wrong"},players,game_id="g1").canonical_player_id=="p2"
    assert reconcile_player({"description":"Same Name"},players,game_id="g1").status=="AMBIGUOUS"
    assert reconcile_player({"description":"Same Name","team":"BUF"},players,game_id="g1").canonical_player_id=="p1"
    same_team=players+[{"game_id":"g1","player_id":"p3","player_name":"Same Name","team":"BUF"}]
    assert reconcile_player({"description":"Same Name","team":"BUF"},same_team,game_id="g1").status=="AMBIGUOUS"

def test_reconciliation_index_is_game_scoped():
    from backtesting.player_prop_odds import build_player_history_index
    index=build_player_history_index([
        {"game_id":"g1","player_id":"p1","player_name":"Shared Name","team":"BUF"},
        {"game_id":"g2","player_id":"p2","player_name":"Shared Name","team":"BUF"}])
    assert reconcile_player({"description":"Shared Name"},index,game_id="g1").canonical_player_id=="p1"
    assert reconcile_player({"description":"Shared Name"},index,game_id="g2").canonical_player_id=="p2"
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

def test_category_split_outcomes_aggregate_and_grade_every_supported_market():
    rows=[
        {"game_id":"g1","canonical_player_id":"p1","player_name":"Utility Star","team":"BUF",
         "season":2025,"week":1,"category":"passing","source_id":"pass-row",
         "stats":{"passing_yards":275,"passing_touchdowns":2}},
        {"game_id":"g1","canonical_player_id":"p1","player_name":"Utility Star","team":"BUF",
         "season":2025,"week":1,"category":"rushing","source_id":"rush-row",
         "stats":{"carries":7,"rushing_yards":41}},
        {"game_id":"g1","canonical_player_id":"p1","player_name":"Utility Star","team":"BUF",
         "season":2025,"week":1,"category":"receiving","source_id":"receive-row",
         "stats":{"receptions":3,"receiving_yards":26}},
    ]
    outcomes, diagnostics=aggregate_player_outcomes(rows)
    assert list(outcomes) == [("g1","p1")]
    assert diagnostics == {"raw_outcome_rows":3,"canonical_player_outcomes":1,
        "duplicate_fields_merged":0,"conflicting_fields":0,
        "players_with_multiple_category_rows":1,"conflicts":[]}
    outcome=outcomes[("g1","p1")]
    assert outcome["source_row_count"] == 3
    assert outcome["source_categories"] == ["passing","receiving","rushing"]
    expected={"passing_yards":275,"passing_tds":2,"rushing_attempts":7,
              "rushing_yards":41,"receptions":3,"receiving_yards":26}
    for market, actual in expected.items():
        quote={"game_id":"g1","canonical_player_id":"p1","market":market,
               "line":actual-.5,"selection":"OVER"}
        assert grade_quote(quote,outcomes)["actual_stat"] == actual

def test_outcome_aggregation_duplicate_missing_conflict_and_scoping():
    base={"game_id":"g1","canonical_player_id":"p1","team":"BUF","category":"passing"}
    outcomes, diagnostics=aggregate_player_outcomes([
        {**base,"stats":{"passing_yards":None,"passing_tds":0}},
        {**base,"category":"summary","stats":{"passing_yards":10,"passing_tds":0}},
        {**base,"game_id":"g2","stats":{"passing_yards":20}},
        {**base,"canonical_player_id":"p2","stats":{"passing_yards":30}},
    ])
    assert outcomes[("g1","p1")]["stats"] == {"passing_tds":0,"passing_yards":10}
    assert diagnostics["duplicate_fields_merged"] == 1
    assert set(outcomes) == {("g1","p1"),("g2","p1"),("g1","p2")}
    zero_quote={"game_id":"g1","canonical_player_id":"p1","market":"passing_tds",
                "line":.5,"selection":"UNDER"}
    assert grade_quote(zero_quote,outcomes)["result"] == "win"
    missing_quote={**zero_quote,"market":"receiving_yards"}
    with pytest.raises(ValueError,match="outcome market is missing"):
        grade_quote(missing_quote,outcomes)
    with pytest.raises(ValueError,match=r'canonical_player_id.*p1.*passing_yards.*game_id.*g1'):
        aggregate_player_outcomes([base|{"stats":{"passing_yards":10}},
                                   base|{"category":"summary","stats":{"passing_yards":11}}])

@pytest.mark.parametrize("row", [
    {"game_id":"g1","stats":{"passing_yards":1}},
    {"game_id":"g1","canonical_player_id":"null","stats":{"passing_yards":1}},
])
def test_outcome_aggregation_rejects_unresolved_player_id(row):
    with pytest.raises(ValueError,match="unresolved canonical player ID"):
        aggregate_player_outcomes([row])

@pytest.mark.parametrize(("outcome", "quote_id"), [
    ({"canonical_player_id": 123456}, "123456"),
    ({"athlete_id": 123456}, "123456"),
    ({"canonical_player_id": "123456"}, 123456),
    ({"canonical_player_id": " 123456 "}, "  123456  "),
])
def test_outcome_keys_normalize_mixed_player_id_representations(outcome, quote_id):
    rows = [{"game_id": " g1 ", **outcome, "stats": {"receptions": 4}}]
    outcomes, _ = aggregate_player_outcomes(rows)
    assert list(outcomes) == [("g1", "123456")]
    assert outcomes[("g1", "123456")]["canonical_player_id"] == "123456"
    quote = {"game_id": "g1", "canonical_player_id": quote_id, "market": "receptions",
             "line": 3.5, "selection": "OVER"}
    assert grade_quote(quote, outcomes)["result"] == "win"

def test_outcome_ids_remain_distinct_and_provider_id_is_provenance():
    outcomes, _ = aggregate_player_outcomes([
        {"game_id": "g1", "athlete_id": 123, "stats": {"receptions": 1}},
        {"game_id": "g1", "athlete_id": "124", "stats": {"receptions": 2}},
    ])
    assert set(outcomes) == {("g1", "123"), ("g1", "124")}
    assert outcomes[("g1", "123")]["source_provider_ids"] == ["123"]

@pytest.mark.parametrize("value", [None, "", "   ", "UNKNOWN", " unknown "])
def test_outcome_invalid_ids_fail_closed(value):
    with pytest.raises(ValueError, match="unresolved canonical player ID"):
        aggregate_player_outcomes([{"game_id": "g1", "canonical_player_id": value,
                                    "stats": {"receptions": 1}}])

def test_grade_quote_distinguishes_missing_and_multiple_outcomes():
    quote = {"game_id": "g1", "canonical_player_id": " p1 ", "market": "receptions",
             "line": 1.5, "selection": "OVER"}
    with pytest.raises(ValueError, match=r"canonical player outcome not found: game_id=g1, canonical_player_id=p1, market=receptions"):
        grade_quote(quote, {})
    duplicate = [{"game_id": "g1", "player_id": "p1", "receptions": 2}] * 2
    with pytest.raises(ValueError, match=r"multiple canonical player outcomes: game_id=g1, canonical_player_id=p1, market=receptions"):
        grade_quote(quote, duplicate)
def test_offline_audit_partial_and_plan(tmp_path):
    d=tmp_path/"nfl/2025/week_01"; d.mkdir(parents=True); (d/"odds_player_props.json").write_text(json.dumps(quotes()))
    report=audit_cache(tmp_path,season=2025,start_week=1,end_week=2)
    assert report["network_contacted"] is False and report["coverage"]["passing_yards"]["HISTORICAL_PRICE_READY"] == "PARTIAL"
    assert report["PLAYER_PROP_LINE_READY"] == report["PLAYER_PROP_PRICE_READY"] == "PARTIAL"
    assert report["weeks_missing"] == report["price_weeks_missing"] == [2]
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
    assert report["raw_provider_coverage_by_event"]["e1"]=={"player_pass_tds":2,"player_pass_yds":2}


def test_offline_audit_reports_incomplete_cache_rows_without_crashing(tmp_path):
    d=tmp_path/"raw_cache/odds-api/nfl/2025/week_02"; d.mkdir(parents=True)
    (d/"plan.json").write_text(json.dumps({"data":[{
        "game_id":"g2", "week":2, "market":"player_pass_yds",
        "canonical_player_id":"p2"
    }]}))
    report=audit_cache(tmp_path,season=2025,start_week=1,end_week=2)
    assert report["quote_count"] == 0
    assert report["incomplete_quote_row_count"] == 1
    assert report["PLAYER_PROP_PRICE_READY"] == "NOT_READY"

def test_audit_identity_collision_readiness(tmp_path):
    d=tmp_path/"nfl/2025/week_01"; d.mkdir(parents=True)
    rows=quotes()+[{**quotes()[0],"provider_player_name":"Materially Different","team":"MIA"}]
    (d/"player_prop_odds.json").write_text(json.dumps(rows))
    report=audit_cache(tmp_path,season=2025,start_week=1,end_week=1)
    assert report["identity_collision_count"]==1 and report["PLAYER_IDENTITY_READY"]=="NOT_READY"


def test_audit_allows_same_player_to_change_teams_between_games(tmp_path):
    first=quotes()[0]
    rows=[first,{**first,"game_id":"g2","week":2,"team":"MIA"}]
    for week in (1,2):
        d=tmp_path/f"nfl/2025/week_{week:02d}"; d.mkdir(parents=True)
        (d/"player_prop_odds.json").write_text(json.dumps([rows[week-1]]))
    report=audit_cache(tmp_path,season=2025,start_week=1,end_week=2)
    assert report["identity_collision_count"]==0
    assert report["PLAYER_IDENTITY_READY"]=="READY"
def test_missing_pricing_and_fair_sgp_label():
    assert availability([],requested_weeks=[1])["passing_yards"]["HISTORICAL_PRICE_READY"] == "NOT_READY"
    fair=simulation_fair_sgp_price(.25); assert fair["simulation_fair_decimal_odds"] == 4 and fair["sportsbook_ev"] is None and "MODEL_FAIR" in fair["price_type"]


def test_availability_reports_incomplete_rows_without_crashing():
    report = availability([
        {"market": "passing_yards", "week": 1},
        {"market": "passing_yards", "week": 1, "bookmaker": "book-a"},
    ], requested_weeks=[1, 2])
    assert report["passing_yards"]["HISTORICAL_PRICE_READY"] == "PARTIAL"
    assert report["passing_yards"]["bookmakers"] == ["book-a"]
