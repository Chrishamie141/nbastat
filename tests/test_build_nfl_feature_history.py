import json
from argparse import Namespace
from pathlib import Path

from backtesting.build_nfl_feature_history import _read_list, build_week, make_plan
from backtesting.historical_provider import HistoricalSnapshotProvider
from nfl_providers import EspnNflProvider, JsonRawCache, normalize_espn_player_boxscore
from backtesting.player_prop_odds import aggregate_player_outcomes, grade_quote


def _scoreboard():
    competitors=[
        {"homeAway":"home","score":"24","team":{"abbreviation":"KC"}},
        {"homeAway":"away","score":"17","team":{"abbreviation":"BAL"}},
    ]
    return {"events":[{"id":"401","date":"2024-09-06T00:20:00Z",
        "status":{"type":{"completed":True,"name":"STATUS_FINAL"}},
        "competitions":[{"competitors":competitors,"venue":{"fullName":"Arrowhead"}}]}]}


def _summary():
    athlete={"athlete":{"id":"15","displayName":"Patrick Mahomes","position":{"abbreviation":"QB"}},
             "stats":["20/30","250","2","0"]}
    return {"boxscore":{"players":[{"team":{"abbreviation":"KC"},"statistics":[
        {"name":"passing","labels":["C/ATT","YDS","TD","INT"],"athletes":[athlete]}]}]}}


def _args(tmp_path):
    return Namespace(season=2024,start_week=1,end_week=1,snapshot_root=tmp_path/"snapshots",
                     cache_root=tmp_path/"cache",resume=False,validate=True,plan=False,allow_network=True,
                     game_id=None,rebuild_from_cache=False)


def test_feature_build_is_cached_deterministic_and_has_no_paid_requests(tmp_path, monkeypatch):
    args=_args(tmp_path); cache=JsonRawCache(args.cache_root); provider=EspnNflProvider(cache)
    monkeypatch.setattr(provider,"_scoreboard",lambda season,week:_scoreboard())
    monkeypatch.setattr(provider,"_summary",lambda season,week,event:_summary())
    first=build_week(args,provider,1)
    before={p.name:p.read_bytes() for p in (args.snapshot_root/"nfl/2024/week_01").glob("*.json") if p.name not in {"manifest.json","metadata.json"}}
    second=build_week(args,provider,1)
    after={p.name:p.read_bytes() for p in (args.snapshot_root/"nfl/2024/week_01").glob("*.json") if p.name not in {"manifest.json","metadata.json"}}
    assert first["datasets"]["player_stats"] == 1
    assert first["datasets"]["team_stats"] == 2
    assert before == after
    manifest=json.loads((args.snapshot_root/"nfl/2024/week_01/manifest.json").read_text())
    assert manifest["paid_requests_required"] == manifest["estimated_paid_credits"] == 0
    assert second["diagnostics"][0]["passing_rows_emitted"] == 1


def test_plan_is_offline_and_prior_season_rows_are_leakage_safe(tmp_path):
    args=_args(tmp_path); provider=EspnNflProvider(JsonRawCache(args.cache_root))
    plan=make_plan(args,provider)
    assert plan["network_contacted"] is False
    assert plan["paid_requests_required"] == plan["estimated_paid_credits"] == 0
    monkey_game={"game_id":"espn-2025","league":"nfl","season":"2025","week":1,
                 "kickoff_time":"2025-09-05T00:00:00Z","home_team":"KC","away_team":"BAL"}
    week=args.snapshot_root/"nfl/2025/week_01"; week.mkdir(parents=True)
    (week/"games.json").write_text(json.dumps([monkey_game]))
    (week/"team_stats.json").write_text("[]")
    old=args.snapshot_root/"nfl/2024/week_01"; old.mkdir(parents=True)
    (old/"games.json").write_text(json.dumps([{"game_id":"old","season":"2024","week":1,"kickoff_time":"2024-09-01T00:00:00Z"}]))
    (old/"player_stats.json").write_text(json.dumps([{"game_id":"old","player_id":"15","player":"Patrick Mahomes",
        "team":"KC","season":2024,"week":1,"stats":{"passing_yards":250},"completed_at":"2024-09-01T06:00:00Z",
        "record_role":"completed_game_history","is_pregame":False}]))
    (old/"team_stats.json").write_text(json.dumps([{"game_id":"old","team":"KC","opponent":"BAL",
        "season":2024,"week":1,"points_for":24,"points_against":17,"completed_at":"2024-09-01T06:00:00Z",
        "record_role":"completed_game_history","is_pregame":False}]))
    views=HistoricalSnapshotProvider(args.snapshot_root).get_game_histories("nfl","2025",1,monkey_game)
    assert len(views.player_history.rows) == 1
    assert len(views.target_team_history.rows) == 1


def test_malformed_summary_is_diagnosed_without_losing_other_datasets(tmp_path, monkeypatch):
    args=_args(tmp_path); provider=EspnNflProvider(JsonRawCache(args.cache_root))
    monkeypatch.setattr(provider,"_scoreboard",lambda season,week:_scoreboard())
    monkeypatch.setattr(provider,"_summary",lambda season,week,event:{"unexpected":[]})
    report=build_week(args,provider,1)
    assert report["datasets"]["outcomes"] == 1
    assert report["datasets"]["player_stats"] == 0
    assert report["diagnostics"][0]["classification"] == "MALFORMED_OR_UNAVAILABLE_ESPN_SUMMARY"


def test_realistic_dal_philadelphia_fixture_preserves_ids_and_grades_all_markets():
    fixture=Path("tests/fixtures/espn_summary_401772510.json")
    payload=json.loads(fixture.read_text()); diagnostics={}
    rows=normalize_espn_player_boxscore(payload,"2025",1,diagnostics)
    dak=[row for row in rows if row["provider_player_id"] == "2577417"]
    assert {row["category"] for row in dak} == {"passing","rushing"}
    assert all(row["canonical_player_id"] == row["athlete_id"] == row["player_id"] == "2577417" for row in dak)
    assert next(row for row in dak if row["category"] == "passing")["stats"] == {
        "completions":21,"passing_attempts":34,"passing_yards":188,"passing_tds":0,"interceptions":0}
    assert next(row for row in dak if row["category"] == "rushing")["stats"]["rushing_attempts"] == 1
    assert diagnostics["passing_rows_emitted"] == 1
    normalized=[]
    for row in rows:
        normalized.append({**row,"game_id":"espn-401772510","record_role":"completed_game_history",
                           "is_pregame":False,"week":1})
    outcomes,_=aggregate_player_outcomes(normalized)
    markets={"passing_yards":180.5,"passing_tds":0.5,"rushing_attempts":3.5,
             "rushing_yards":18.5,"receptions":6.5,"receiving_yards":100.5}
    players={market:("2577417" if market.startswith(("passing","rushing")) else "15818") for market in markets}
    for market,line in markets.items():
        result=grade_quote({"game_id":"espn-401772510","canonical_player_id":players[market],
                            "market":market,"line":line,"selection":"OVER"},outcomes)
        assert result["actual_stat"] is not None


def test_feature_build_preserves_offensive_values_through_json_and_grading(tmp_path, monkeypatch):
    """Exercise raw summary -> parser -> canonical row -> JSON -> evaluator."""
    payload=json.loads(Path("tests/fixtures/espn_summary_401772510.json").read_text())
    scoreboard=_scoreboard()
    event=scoreboard["events"][0]
    event["id"]="401772510"
    event["competitions"][0]["competitors"]=[
        {"homeAway":"home","score":"20","team":{"abbreviation":"PHI"}},
        {"homeAway":"away","score":"17","team":{"abbreviation":"DAL"}},
    ]
    args=_args(tmp_path)
    provider=EspnNflProvider(JsonRawCache(args.cache_root))
    monkeypatch.setattr(provider,"_scoreboard",lambda season,week:scoreboard)
    monkeypatch.setattr(provider,"_summary",lambda season,week,event_id:payload)

    report=build_week(args,provider,1)
    path=args.snapshot_root/"nfl/2024/week_01/player_stats.json"
    restored=_read_list(path)
    dak=[row for row in restored if row["provider_player_id"] == "2577417"]

    assert report["datasets"]["player_stats"] == 3
    assert {row["category"] for row in dak} == {"passing","rushing"}
    passing=next(row for row in dak if row["category"] == "passing")
    rushing=next(row for row in dak if row["category"] == "rushing")
    assert (passing["passing_yards"],passing["passing_tds"]) == (188,0)
    assert (rushing["rushing_attempts"],rushing["rushing_yards"]) == (1,3)
    assert "receiving_yards" not in passing and "receiving_yards" not in rushing

    outcomes,_=aggregate_player_outcomes(restored)
    grade=grade_quote({"game_id":"espn-401772510","canonical_player_id":"2577417",
                       "market":"passing_tds","line":0.5,"selection":"UNDER"},outcomes)
    assert grade["actual_stat"] == 0
    assert grade["result"] == "win"


def test_cache_only_provider_never_fetches_missing_payload(tmp_path):
    provider=EspnNflProvider(JsonRawCache(tmp_path),allow_network=False)
    try:
        provider._summary(2025,1,"401772510")
    except Exception as exc:
        assert "cache-only ESPN summary missing" in str(exc)
    else:
        raise AssertionError("cache-only mode unexpectedly fetched a response")
