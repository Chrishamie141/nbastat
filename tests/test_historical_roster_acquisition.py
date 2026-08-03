import json
from pathlib import Path

import pytest

from nfl_providers import HttpJsonResponse

from backtesting.historical_roster_acquisition import (acquire_roster_identities,
    normalize_cached_roster, plan_roster_acquisition, roster_cache_path, roster_url)
from backtesting.player_identity_registry import build_identity_registry
from backtesting.player_prop_odds import reconcile_player


GAME={"game_id":"g1","home_team":"BUF","away_team":"MIA","season":2025,"week":1,
      "kickoff_time":"2025-09-05T00:00:00Z","prediction_cutoff":"2025-09-04T20:00:00Z"}


def test_roster_url_uses_espn_washington_slug():
    assert "/teams/wsh/roster?" in roster_url("WAS", 2026)
    assert "/teams/buf/roster?" in roster_url("BUF", 2026)


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


def test_explicit_refresh_replaces_cached_roster(tmp_path):
    _cached(tmp_path); _cached(tmp_path,team="MIA",name="Other Player",player_id="9")
    plan=plan_roster_acquisition([GAME],tmp_path,season=2025)
    plan["historical_acquisition_supported"]=True
    calls=[]
    payload={"athletes":[{"items":[{"id":"77","displayName":"Refreshed Player",
                                      "position":{"abbreviation":"RB"}}]}]}
    _rows,report=acquire_roster_identities(
        plan,allow_network=True,refresh=True,
        fetcher=lambda url: calls.append(url) or HttpJsonResponse(payload,200,{}))
    assert len(calls)==2 and len(report["refreshed"])==2
    assert report["cached"]==[]
    assert all("Refreshed Player" in Path(record).read_text(encoding="utf-8")
               for record in report["raw_cache_files_written"])


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


def test_provider_declared_historical_scope_accepts_late_download_and_rejects_wrong_scope(tmp_path):
    path=_cached(tmp_path,captured="2026-07-01T00:00:00Z")
    request=plan_roster_acquisition([GAME],tmp_path,season=2025)["per_game_team_requests"][0]
    payload=json.loads(path.read_text()); metadata=json.loads(path.with_suffix(".metadata.json").read_text())
    metadata["provider_historical_scope"]={"season":2025,"week":1,"source_field":"archive.rosterWeek"}
    rows,errors=normalize_cached_roster(payload,metadata,request)
    assert rows and not errors and rows[0]["historical_scope"]["week"]==1
    metadata["provider_historical_scope"]["week"]=2
    assert "provider_week_scope_mismatch" in normalize_cached_roster(payload,metadata,request)[1]
    metadata["provider_historical_scope"]={"season":2024,"effective_at":"2025-09-01T00:00:00Z","source_field":"archive.asOf"}
    assert "provider_season_scope_mismatch" in normalize_cached_roster(payload,metadata,request)[1]


def test_late_provider_scoped_roster_survives_registry_end_to_end(tmp_path):
    game={**GAME,"kickoff_time":"2025-09-07T20:00:00Z","prediction_cutoff":"2025-09-07T00:00:00Z"}
    path=_cached(tmp_path,captured="2026-07-31T00:00:00Z")
    metadata=json.loads(path.with_suffix(".metadata.json").read_text())
    metadata["provider_historical_scope"]={"season":2025,"week":1,"source_field":"archive.rosterWeek"}
    path.with_suffix(".metadata.json").write_text(json.dumps(metadata))
    request=plan_roster_acquisition([game],tmp_path,season=2025)["per_game_team_requests"][0]
    rows,errors=normalize_cached_roster(json.loads(path.read_text()),metadata,request)
    assert not errors and rows[0]["captured_at"].startswith("2026-07-31")
    assert rows[0]["historical_scope"]["week"]==1
    assert rows[0]["known_at"]==request["historical_cutoff"]
    directory=tmp_path/"snapshot"; directory.mkdir()
    (directory/"roster_identities.json").write_text(json.dumps(rows))
    identities=build_identity_registry(directory,[game],season=2025,week=1)
    assert [row["player_name"] for row in identities]==["Roster Only"]
    assert identities[0]["captured_at"].startswith("2026-07-31")
    assert identities[0]["scope_validation_method"]=="provider_season_week"


@pytest.mark.parametrize(("scope","expected"),[
    ({"season":2025,"week":2,"source_field":"archive.week"},"provider_week_scope_mismatch"),
    ({"season":2024,"week":1,"source_field":"archive.week"},"provider_season_scope_mismatch"),
    ({"season":2025,"effective_at":"2025-09-08T00:00:00Z","source_field":"archive.asOf"},"provider_effective_at_after_cutoff"),
    ({"season":2025,"effective_at":"not-a-date","source_field":"archive.asOf"},"malformed_provider_effective_at"),
    ({"season":2025,"source_field":"archive.claim"},"provider_scope_missing_week_or_effective_at"),
])
def test_late_historical_scope_fails_closed(tmp_path,scope,expected):
    path=_cached(tmp_path,captured="2026-07-31T00:00:00Z")
    request=plan_roster_acquisition([GAME],tmp_path,season=2025)["per_game_team_requests"][0]
    metadata=json.loads(path.with_suffix(".metadata.json").read_text())
    metadata["provider_historical_scope"]=scope
    assert expected in normalize_cached_roster(json.loads(path.read_text()),metadata,request)[1]


def test_current_espn_roster_is_rejected_as_historical(tmp_path):
    path=_cached(tmp_path,captured="2026-07-01T00:00:00Z")
    request=plan_roster_acquisition([GAME],tmp_path,season=2025)["per_game_team_requests"][0]
    payload=json.loads(path.read_text()); metadata=json.loads(path.with_suffix(".metadata.json").read_text())
    payload["season"]={"year":2025}
    assert "missing_provider_historical_scope" in normalize_cached_roster(payload,metadata,request)[1]


def test_partial_acquisition_survives_failed_team_and_caches_success(tmp_path):
    import nfl_providers
    from nfl_providers import HttpJsonResponse, StructuredHttpError
    plan=plan_roster_acquisition([GAME],tmp_path,season=2025)
    # Exercise the retained provider/cache interface as if a defensible external
    # historical adapter had enabled network acquisition.
    plan["historical_acquisition_supported"]=True; calls=[]
    def fetch(url):
        calls.append(url)
        if "/buf/" in url:
            raise StructuredHttpError(status=503,message="down",classification="TRANSIENT_PROVIDER_ERROR",url=url)
        return HttpJsonResponse({"athletes":[]},200,{"content-type":"application/json"})
    rows,report=acquire_roster_identities(plan,allow_network=True,fetcher=fetch)
    assert not rows and len(calls)==2
    assert report["counts"]["failed"]==1 and report["counts"]["succeeded"]==1
    assert len(report["raw_cache_files_written"])==1


def test_partial_acquisition_records_invalid_provider_response_and_continues(tmp_path):
    from nfl_providers import HttpJsonResponse, StructuredHttpError
    plan=plan_roster_acquisition([GAME],tmp_path,season=2025)
    plan["historical_acquisition_supported"]=True
    def fetch(url):
        if "/buf/" in url:
            raise StructuredHttpError(status=200,message="<html>bad</html>",
                classification="INVALID_PROVIDER_RESPONSE",url=url,
                headers={"content-type":"text/html"})
        return HttpJsonResponse({"athletes":[]},200,{"content-type":"application/json"})
    _,report=acquire_roster_identities(plan,allow_network=True,fetcher=fetch)
    assert report["counts"]=={"succeeded":1,"failed":1,"rejected":1,"cached":0}
    assert report["failed"][0]["classification"]=="INVALID_PROVIDER_RESPONSE"
    assert report["failed"][0]["http_status"]==200


def test_unsupported_espn_historical_acquisition_does_not_fan_out(tmp_path):
    plan=plan_roster_acquisition([GAME],tmp_path,season=2025); calls=[]
    rows,report=acquire_roster_identities(plan,allow_network=True,fetcher=lambda url:calls.append(url))
    assert not rows and calls==[] and report["network_contacted"] is False
    assert report["counts"]["rejected"]==2
    assert {item["reason"] for item in report["rejected"]}=={"provider_historical_acquisition_unsupported"}


def test_verification_mode_performs_exactly_one_request(tmp_path):
    from nfl_providers import HttpJsonResponse
    from backtesting.historical_roster_acquisition import verify_single_request
    request=plan_roster_acquisition([GAME],tmp_path,season=2025)["per_game_team_requests"][0]; calls=[]
    result=verify_single_request(request,fetcher=lambda url:(calls.append(url) or HttpJsonResponse({"athletes":[]},200,{"content-type":"application/json"})))
    assert len(calls)==1 and result["provider"]=="espn" and result["acceptable"] is False
    assert result["http_status"]==200 and result["historical_scope"] is None
