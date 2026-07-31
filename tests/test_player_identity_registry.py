import json

import pytest

from backtesting.build_nfl_player_props import execute
from backtesting.player_identity_registry import build_identity_registry
from backtesting.player_prop_acquisition import cache_path, plan_acquisition
from backtesting.player_prop_odds import normalize_provider_outcomes, reconcile_player


GAME={"game_id":"g1","provider_event_id":"e1","home_team":"BUF","away_team":"MIA",
      "season":2025,"week":1,"kickoff_time":"2025-09-05T00:00:00Z",
      "prediction_cutoff":"2025-09-04T00:20:00Z"}


def _snapshot(tmp_path, games=(GAME,)):
    directory=tmp_path/"snapshots/nfl/2025/week_01"; directory.mkdir(parents=True)
    (directory/"games.json").write_text(json.dumps(list(games)))
    (directory/"odds.json").write_text("[]")
    (directory/"player_stats.json").write_text(json.dumps([
        {"game_id":"g1","athlete_id":"stat-1","player_name":"Stat Player","team":"MIA","passing_yards":10}]))
    (directory/"summary.json").write_text(json.dumps({"id":"g1","boxscore":{"players":[
        {"team":{"abbreviation":"BUF"},"statistics":[{"athletes":[
            {"athlete":{"id":"roster-1","displayName":"Roster Only","position":{"abbreviation":"RB"}}}
        ]}]}]}}))
    return directory


def test_roster_identity_is_separate_from_stats_and_provider_id_wins(tmp_path):
    directory=_snapshot(tmp_path)
    rows=build_identity_registry(directory,[GAME],season=2025,week=1)
    roster=next(r for r in rows if r["player_name"]=="Roster Only")
    assert roster["canonical_player_id"]=="roster-1" and roster["has_stats"] is False
    assert "passing_yards" not in roster
    rec=reconcile_player({"description":"Roster Only","team":"BUF"},rows,game_id="g1")
    assert rec.canonical_player_id=="roster-1" and rec.status=="EXACT_NAME_TEAM_GAME"
    assert next(r for r in rows if r["player_name"]=="Stat Player")["has_stats"] is True


def test_game_team_scope_ambiguity_and_unknown_are_conservative(tmp_path):
    games=(GAME,{**GAME,"game_id":"g2","provider_event_id":"e2","home_team":"NYJ"})
    directory=_snapshot(tmp_path,games)
    (directory/"games.json").write_text(json.dumps([
        {**games[0],"players":[{"player_name":"Same Name","team":"BUF","player_id":"a"},
                               {"player_name":"Same Name","team":"BUF","player_id":"b"}]},
        {**games[1],"players":[{"player_name":"Same Name","team":"NYJ","player_id":"c"}]}]))
    rows=build_identity_registry(directory,json.loads((directory/"games.json").read_text()),season=2025,week=1)
    assert reconcile_player({"description":"Same Name","team":"BUF"},rows,game_id="g1").status=="AMBIGUOUS"
    assert reconcile_player({"description":"Same Name","team":"NYJ"},rows,game_id="g2").canonical_player_id=="c"
    assert reconcile_player({"description":"Absent Everywhere"},rows,game_id="g1").status=="UNKNOWN"


def test_cache_only_rebuild_recovers_roster_only_without_network(tmp_path,monkeypatch):
    directory=_snapshot(tmp_path); cache_root=tmp_path/"cache"
    plan=plan_acquisition([GAME],cache_root,season=2025)
    record=plan["per_game_requests"][0]
    path=cache_path(cache_root,2025,1,"e1",record["requested_snapshot_timestamp"],record["markets"])
    path.parent.mkdir(parents=True)
    outcomes=[{"name":side,"description":"Roster Only","team":"BUF","point":50.5,"price":-110}
              for side in ("Over","Under")]
    path.write_text(json.dumps({"timestamp":"2025-09-04T00:15:00Z","data":{"id":"e1","bookmakers":[
        {"key":"book","markets":[{"key":"player_rush_yds","last_update":"2025-09-04T00:10:00Z","outcomes":outcomes}]}]}}))
    plan=plan_acquisition([GAME],cache_root,season=2025)
    monkeypatch.setattr("backtesting.build_nfl_player_props._fetch_json_structured",lambda *_:pytest.fail("network called"))
    report=execute(plan,tmp_path/"snapshots",cache_root,season=2025,allow_paid=False,resume=True,validate=True)
    persisted=json.loads((directory/"player_prop_odds.json").read_text())
    assert report["network_contacted"] is False and len(persisted)==2
    assert {r["canonical_player_id"] for r in persisted}=={"roster-1"}
    assert all(r["identity_has_stats"] is False for r in persisted)
    assert json.loads((directory/"player_stats.json").read_text())==[
        {"game_id":"g1","athlete_id":"stat-1","player_name":"Stat Player","team":"MIA","passing_yards":10}]
    audit=json.loads((directory/"player_prop_rebuild_audit.json").read_text())
    assert audit["prop_quotes_reconciled_via_roster_only_identity"]==2
