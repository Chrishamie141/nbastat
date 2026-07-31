import hashlib, json
from urllib.parse import parse_qs, urlparse

import pytest

from backtesting import build_nfl_player_props
from backtesting.build_nfl_player_props import PaidBudgetExceeded, execute, persist_week
from backtesting.player_prop_acquisition import cache_path, plan_acquisition
from backtesting.player_prop_odds import (deduplicate_quotes, evaluate_persisted_quotes,
                                          validate_player_prop_rows)
from backtesting.snapshots import snapshot_week_dir
from nfl_providers import HttpJsonResponse, JsonRawCache


GAME={"game_id":"g1","provider_event_id":"e1","season":2025,"week":1,
      "kickoff_time":"2025-09-05T00:00:00Z","prediction_cutoff":"2025-09-04T00:20:00Z"}


def _run_cache_paid_sequence(tmp_path, monkeypatch, capsys, states):
    secret = "valid-production-api-key-1234567"
    assert len(secret) == 32
    games = [{**GAME, "game_id": f"g{index}", "provider_event_id": f"e{index}"}
             for index in range(1, len(states) + 1)]
    directory = snapshot_week_dir(tmp_path / "snapshots", "nfl", 2025, 1)
    directory.mkdir(parents=True)
    (directory / "games.json").write_text(json.dumps(games))
    (directory / "odds.json").write_text("[]")
    (directory / "player_stats.json").write_text("[]")

    cache_root = tmp_path / "cache"
    initial_plan = plan_acquisition(games, cache_root, season=2025)
    cached_event_ids = []
    for record, state in zip(initial_plan["per_game_requests"], states):
        if state != "cached":
            continue
        cached_event_ids.append(record["provider_event_id"])
        path = cache_path(cache_root, 2025, 1, record["provider_event_id"],
                          record["requested_snapshot_timestamp"], record["markets"])
        path.parent.mkdir(parents=True, exist_ok=True)
        # Iterating this market used to overwrite the local credential variable.
        payload = {"timestamp": "2025-09-04T00:15:00Z", "data": {
            "id": record["provider_event_id"], "bookmakers": [{"key": "book",
                "markets": [{"key": "player_pass_yds", "outcomes": []}]}]}}
        path.write_text(json.dumps(payload))

    plan = plan_acquisition(games, cache_root, season=2025)
    requested_event_ids = []
    outgoing_keys = []

    def fake_fetch(url):
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        outgoing_keys.append(query["apiKey"][0])
        event_id = parsed.path.split("/events/", 1)[1].split("/", 1)[0]
        requested_event_ids.append(event_id)
        return HttpJsonResponse({"timestamp": "2025-09-04T00:15:00Z",
            "data": {"id": event_id, "bookmakers": []}}, 200,
            {"x-requests-remaining": "100"})

    monkeypatch.setenv("THE_ODDS_API_KEY", secret)
    monkeypatch.delenv("ODDS_API_KEY", raising=False)
    monkeypatch.setattr(build_nfl_player_props, "_fetch_json_structured", fake_fetch)
    report = execute(plan, tmp_path / "snapshots", cache_root, season=2025,
                     allow_paid=True, resume=True, validate=True, sleeper=lambda _: None)

    paid_event_ids = [game["provider_event_id"] for game, state in zip(games, states)
                      if state == "paid"]
    assert requested_event_ids == paid_event_ids
    assert not set(cached_event_ids) & set(requested_event_ids)
    assert outgoing_keys == [secret] * len(paid_event_ids)
    assert all(value != "player_pass_yds" for value in outgoing_keys)
    assert report["validated_cache_hits"] == states.count("cached")
    assert report["paid_attempts"] == report["paid_successes"] == states.count("paid")
    assert report["paid_failures"] == 0
    assert report["paid_requests_made"] == states.count("paid")

    output = capsys.readouterr().out
    assert secret not in output
    assert secret not in json.dumps(report)
    assert secret not in json.dumps(JsonRawCache.identity(
        "odds-api", "nfl", 2025, 1, "event-player-props",
        {"apiKey": secret, "markets": "player_pass_yds"}))
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert secret not in path.name
            assert secret.encode() not in path.read_bytes()


def test_cached_event_cannot_shadow_api_key_for_next_paid_event(tmp_path, monkeypatch, capsys):
    _run_cache_paid_sequence(tmp_path, monkeypatch, capsys, ["cached", "paid"])


def test_api_key_is_stable_across_cached_paid_cached_paid_sequence(tmp_path, monkeypatch, capsys):
    _run_cache_paid_sequence(tmp_path, monkeypatch, capsys,
                             ["cached", "paid", "cached", "paid"])


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
