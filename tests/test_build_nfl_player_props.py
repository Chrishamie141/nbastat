import hashlib, json

import pytest

from backtesting.build_nfl_player_props import PaidBudgetExceeded, persist_week
from backtesting.player_prop_acquisition import cache_path, plan_acquisition
from backtesting.player_prop_odds import (deduplicate_quotes, evaluate_persisted_quotes,
                                          validate_player_prop_rows)


GAME={"game_id":"g1","provider_event_id":"e1","season":2025,"week":1,
      "kickoff_time":"2025-09-05T00:00:00Z","prediction_cutoff":"2025-09-04T00:20:00Z"}


def test_plan_is_multimarket_exact_and_read_only(tmp_path,monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen",lambda *a,**k:pytest.fail("network called"))
    before=list(tmp_path.rglob("*")); plan=plan_acquisition([GAME],tmp_path,season=2025)
    assert list(tmp_path.rglob("*"))==before
    assert plan["provider_requests"]==plan["paid_requests_required"]==1
    assert plan["estimated_credits"]==60 and len(plan["markets_requested"])==6


def test_valid_and_malformed_cache_accounting(tmp_path):
    keys=plan_acquisition([GAME],tmp_path,season=2025)["markets_requested"]
    path=cache_path(tmp_path,2025,1,"e1",GAME["prediction_cutoff"],keys); path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"timestamp":"2025-09-04T00:15:38Z","data":{"id":"e1","bookmakers":[]}}))
    assert plan_acquisition([GAME],tmp_path,season=2025)["paid_requests_required"]==0
    path.write_text("{")
    plan=plan_acquisition([GAME],tmp_path,season=2025)
    assert plan["invalid_cache_entries"]==1 and plan["paid_requests_required"]==1


def test_validation_timestamp_and_zero_line():
    base={"game_id":"g1","canonical_player_id":"p1","market":"passing_tds","bookmaker":"b","line":0.0,
          "american_odds":-110,"selection":"OVER","provider_snapshot_timestamp":"2025-09-04T00:15:00Z",
          "market_last_update":"2025-09-04T00:10:00Z","reconciliation_status":"matched"}
    players=[{"game_id":"g1","player_id":"p1"}]
    assert validate_player_prop_rows([base],[GAME],players)==[]
    # The archive envelope may precede a bookmaker update.  The requested as-of
    # timestamp, rather than the envelope identity, is the leakage boundary.
    assert validate_player_prop_rows([{**base,"market_last_update":"2025-09-04T00:16:00Z"}],[GAME],players)==[]
    errors=validate_player_prop_rows([{**base,"requested_snapshot_timestamp":"2025-09-04T00:15:30Z",
                                       "market_last_update":"2025-09-04T00:16:00Z"}],[GAME],players)
    assert any("market_update_after_requested_snapshot" in e for e in errors)


def test_dedup_contract_preserves_side_line_and_book():
    base={"league":"nfl","season":2025,"week":1,"game_id":"g1","canonical_player_id":"p1",
          "market":"passing_yards","bookmaker":"a","line":250.5,"selection":"OVER",
          "provider_snapshot_timestamp":"2025-09-04T00:15:00Z","market_last_update":"2025-09-04T00:10:00Z","american_odds":-110}
    rows,diag=deduplicate_quotes([base,dict(base),{**base,"selection":"UNDER"},
        {**base,"line":251.5},{**base,"bookmaker":"b"}])
    assert len(rows)==4 and diag["duplicate_exact"]==1 and diag["duplicate_conflict"]==0
    rows,diag=deduplicate_quotes([base,{**base,"american_odds":-105,"market_last_update":"2025-09-04T00:11:00Z"}])
    assert len(rows)==1 and rows[0]["american_odds"]==-105 and diag["duplicate_conflict"]==1


def test_persistence_manifest_and_unrelated_bytes(tmp_path):
    unrelated={name:(name+"\n").encode() for name in ("games.json","odds.json","team_stats.json","player_stats.json","outcomes.json")}
    for name,data in unrelated.items(): (tmp_path/name).write_bytes(data)
    row={"game_id":"g1","canonical_player_id":"p1","market":"passing_yards","bookmaker":"b","line":0,
         "selection":"OVER","provider_snapshot_timestamp":"2025-09-04T00:15:00Z","requested_snapshot_timestamp":GAME["prediction_cutoff"],"source":"the-odds-api-historical"}
    persist_week(tmp_path,[row]); first=(tmp_path/"player_prop_odds.json").read_bytes(); persist_week(tmp_path,[row])
    assert (tmp_path/"player_prop_odds.json").read_bytes()==first
    manifest=json.loads((tmp_path/"manifest.json").read_text()); assert manifest["datasets"]["player_prop_odds"]["sha256"]==hashlib.sha256(first).hexdigest()
    assert all((tmp_path/name).read_bytes()==data for name,data in unrelated.items())


def test_offline_simulation_evaluation_plumbing():
    quote={"game_id":"g1","canonical_player_id":"p1","market":"passing_yards","bookmaker":"b","line":250,
           "selection":"OVER","snapshot_timestamp":"t","decimal_odds":2,"implied_probability":.5,"week":1}
    outcome={"game_id":"g1","player_id":"p1","passing_yards":251}
    report=evaluate_persisted_quotes([quote],[outcome],{("g1","p1","passing_yards",250,"OVER"):.6})
    assert report["evaluated_quotes"][0]["model_edge"]==pytest.approx(.1)
    assert report["evaluated_quotes"][0]["grade"]=="WIN" and report["historical_sgp_book_price_ready"] is False
