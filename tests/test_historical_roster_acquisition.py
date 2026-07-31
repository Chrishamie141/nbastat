import json

import pytest

from backtesting.historical_roster_acquisition import (acquire_roster_identities,
    normalize_cached_roster, plan_roster_acquisition, roster_cache_path)
from backtesting.player_identity_registry import build_identity_registry
from backtesting.player_prop_odds import reconcile_player


GAME={"game_id":"g1","home_team":"BUF","away_team":"MIA","season":2025,"week":1,
      "kickoff_time":"2025-09-05T00:00:00Z","prediction_cutoff":"2025-09-04T20:00:00Z"}


def _cached(tmp_path, *, team="BUF", captured="2025-09-04T19:00:00Z", name="Roster Only", player_id="42"):
    path=roster_cache_path(tmp_path,2025,1,team); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({"athletes":[{"items":[{"id":player_id,"displayName":name,
        "position":{"abbreviation":"RB"}}]}]}),encoding="utf-8")
    identity={"provider":"espn","sport":"nfl","season":"2025","week":1,"endpoint":"team-roster",
              "params":{"team":team,"season":2025}}
    path.with_suffix(".metadata.json").write_text(json.dumps({"request_timestamp":captured,
        "request_identity":identity}),encoding="utf-8")
    return path


def test_plan_is_read_only_cache_first_and_free(tmp_path,monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen",lambda *_a,**_k:pytest.fail("network called"))
    before=list(tmp_path.rglob("*")); plan=plan_roster_acquisition([GAME],tmp_path,season=2025)
    assert list(tmp_path.rglob("*"))==before
    assert plan["requests_required"]==2 and plan["network_required"] is True
    assert plan["paid_quota_estimate"]==0 and plan["network_contacted"] is False


def test_cached_historical_roster_normalizes_and_reconciles_without_stats(tmp_path):
    _cached(tmp_path); _cached(tmp_path,team="MIA",name="Other Player",player_id="9")
    plan=plan_roster_acquisition([GAME],tmp_path,season=2025)
    rows,report=acquire_roster_identities(plan,fetcher=lambda *_:pytest.fail("network called"))
    assert report["network_contacted"] is False and len(rows)==2
    directory=tmp_path/"snapshots"; directory.mkdir(); (directory/"roster_identities.json").write_text(json.dumps(rows))
    identities=build_identity_registry(directory,[GAME],season=2025,week=1)
    roster=next(r for r in identities if r["player_name"]=="Roster Only")
    assert roster["canonical_player_id"]=="42" and roster["has_stats"] is False
    assert not ({"attempts","receptions","yards","passing_yards"} & roster.keys())
    assert reconcile_player({"description":"Roster Only","team":"BUF"},identities,game_id="g1").canonical_player_id=="42"


def test_future_capture_and_wrong_scope_are_rejected(tmp_path):
    path=_cached(tmp_path,captured="2026-01-01T00:00:00Z")
    request=plan_roster_acquisition([GAME],tmp_path,season=2025)["per_game_team_requests"][0]
    payload=json.loads(path.read_text()); metadata=json.loads(path.with_suffix(".metadata.json").read_text())
    rows,errors=normalize_cached_roster(payload,metadata,request)
    assert rows==[] and "captured_after_historical_cutoff" in errors
    metadata["request_timestamp"]="2025-09-04T19:00:00Z"; metadata["request_identity"]["params"]["season"]=2026
    assert "season_scope_mismatch" in normalize_cached_roster(payload,metadata,request)[1]


def test_same_name_provider_ids_remain_ambiguous_and_output_is_deterministic(tmp_path):
    directory=tmp_path/"week"; directory.mkdir()
    evidence=[{"player_name":"Same Name","provider_player_id":pid,"team":"BUF","game_id":"g1",
        "season":2025,"week":1,"source":"historical_roster","known_at":"2025-09-04T19:00:00Z"}
        for pid in ("2","1")]
    (directory/"roster_identities.json").write_text(json.dumps(evidence))
    first=build_identity_registry(directory,[GAME],season=2025,week=1)
    second=build_identity_registry(directory,[GAME],season=2025,week=1)
    assert first==second
    assert reconcile_player({"description":"Same Name","team":"BUF"},first,game_id="g1").status=="AMBIGUOUS"


def test_credentials_never_enter_roster_cache_identity_or_reports(tmp_path):
    secret="secret-api-key"
    path=_cached(tmp_path); plan=plan_roster_acquisition([GAME],tmp_path,season=2025)
    text=json.dumps(plan)+str(path)+path.with_suffix(".metadata.json").read_text()
    assert secret not in text and "api_key" not in path.name.casefold()
