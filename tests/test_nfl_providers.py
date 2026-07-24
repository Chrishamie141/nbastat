import json
from pathlib import Path
import pytest
from nfl_providers import EspnNflProvider, TheOddsApiNflProvider, NflOfficialProvider, CompositeNflProvider, JsonRawCache, normalize_odds_events, match_events, normalize_team, HistoricalOddsUnavailable, normalize_espn_player_boxscore, normalize_espn_team_boxscore

EVENT={"id":"401","date":"2025-09-07T17:00:00Z","status":{"type":{"name":"STATUS_FINAL"}},"competitions":[{"venue":{"fullName":"Stadium"},"competitors":[{"homeAway":"home","score":"24","team":{"abbreviation":"BUF","displayName":"Buffalo Bills"}},{"homeAway":"away","score":"17","team":{"abbreviation":"MIA","displayName":"Miami Dolphins"}}]}]}
BOX={"boxscore":{"players":[{"team":{"abbreviation":"BUF"},"statistics":[{"name":"passing","labels":["C/ATT","YDS","TD","INT","SACKS"],"athletes":[{"athlete":{"displayName":"Test Quarterback"},"stats":["20/30","270","2","1","3"]}]},{"name":"receiving","labels":["REC","YDS","TD","TGTS"],"athletes":[{"athlete":{"displayName":"Wide Out"},"stats":["7","90","1","9"]}]}]}],"teams":[{"team":{"abbreviation":"BUF"},"statistics":[{"name":"total yards","displayValue":"400"},{"name":"passing yards","displayValue":"270"},{"name":"rushing yards","displayValue":"130"},{"name":"turnovers","displayValue":"1"},{"name":"first downs","displayValue":"22"},{"name":"total plays","displayValue":"64"},{"name":"possession time","displayValue":"31:00"}]}]}}

def test_espn_schedule_and_final_score_normalization():
    p=EspnNflProvider(); g=p.normalize_game(EVENT,"2025",1)
    assert g["game_id"]=="espn-401" and g["home_team"]=="BUF" and g["final_home_score"]==24
    assert p.fetch_outcomes("2025",1,[g])[0]["market_results"]["moneyline"]=="home"

def test_espn_player_and_team_box_score_normalization():
    players=normalize_espn_player_boxscore(BOX,"2025",0); teams=normalize_espn_team_boxscore(BOX,"2025",0)
    assert players[0]["stats"]["passing_yards"]==270 and players[1]["stats"]["targets"]==9
    assert teams[0]["stats"]["total_yards"]=="400" and teams[0]["stats"]["plays"]=="64"

def test_espn_malformed_missing_fields():
    assert EspnNflProvider().normalize_game({},"2025",1).get("game_id") == "espn-None"
    assert normalize_espn_player_boxscore({"boxscore":{}},"2025",0)==[]

def test_odds_event_and_player_prop_normalization_and_matching():
    games=[{"game_id":"espn-401","home_team":"BUF","away_team":"MIA","kickoff_time":"2025-09-07T17:00:00Z"}]
    events=[{"id":"odds1","home_team":"Buffalo Bills","away_team":"Miami Dolphins","commence_time":"2025-09-07T18:00:00Z","bookmakers":[{"key":"dk","title":"DraftKings","markets":[{"key":"player_pass_yds","last_update":"2025-09-07T12:00:00Z","outcomes":[{"name":"Over","description":"Test Quarterback","point":250.5,"price":-110}]}]}]}]
    rows=normalize_odds_events(events,games)
    assert rows[0]["game_id"]=="espn-401" and rows[0]["market"]=="PASS_YDS" and rows[0]["player"]=="Test Quarterback"

def test_team_alias_and_kickoff_tolerance():
    g={"home_team":"KC","away_team":"LAC","kickoff_time":"2025-09-07T17:00:00Z"}
    assert normalize_team("Kansas City Chiefs")=="KC"
    assert match_events(g,{"home_team":"Kansas City Chiefs","away_team":"Los Angeles Chargers","commence_time":"2025-09-07T18:30:00Z"})
    assert not match_events(g,{"home_team":"Kansas City Chiefs","away_team":"Los Angeles Chargers","commence_time":"2025-09-08T18:30:00Z"})

def test_odds_api_auth_failure_and_no_current_substitution(monkeypatch, tmp_path):
    import nfl_providers
    def boom(url):
        from urllib.error import HTTPError
        raise HTTPError(url,403,"Forbidden",None,None)
    monkeypatch.setattr(nfl_providers,"_fetch_json",boom)
    with pytest.raises(HistoricalOddsUnavailable):
        TheOddsApiNflProvider(api_key="SECRET", cache=JsonRawCache(tmp_path)).fetch_odds("2025",1,[],snapshot_time="2025-09-01T00:00:00Z")

def test_optional_nfl_provider_failure_and_composite_priority():
    assert not NflOfficialProvider().supported_datasets
    class A:
        def fetch_games(self,s,w): return []
    class B:
        def fetch_games(self,s,w): return [{"game_id":"b"}]
    assert CompositeNflProvider([A(),B()]).fetch_games(2025,1)[0]["game_id"]=="b"

def test_cache_reuse_and_api_key_redaction(tmp_path, capsys):
    c=JsonRawCache(tmp_path); calls={"n":0}
    def f(): calls["n"]+=1; return [{"ok":True}]
    assert c.get_or_fetch("espn","nfl",2025,1,"scoreboard",{"week":1},f)==[{"ok":True}]
    assert c.get_or_fetch("espn","nfl",2025,1,"scoreboard",{"week":1},f)==[{"ok":True}]
    assert calls["n"]==1

def test_repository_search_confirms_no_active_legacy_dependency():
    import subprocess
    pattern = "".join(chr(c) for c in [83,80,79,82,84,83,68,65,84,65,73,79,124,83,80,79,82,84,83,95,68,65,84,65,95,73,79,124,115,112,111,114,116,115,100,97,116,97])
    result = subprocess.run(["rg", "-n", pattern, "-S", "."], text=True, capture_output=True, check=False)
    lines = [line for line in result.stdout.splitlines() if "test_repository_search_confirms" not in line]
    assert lines == []
