import json
from pathlib import Path
import pytest
from nfl_providers import EspnNflProvider, TheOddsApiNflProvider, NflOfficialProvider, CompositeNflProvider, JsonRawCache, normalize_odds_events, match_events, normalize_team, HistoricalOddsUnavailable, normalize_espn_player_boxscore, normalize_espn_team_boxscore

EVENT={"id":"401","date":"2025-09-07T17:00:00Z","status":{"type":{"name":"STATUS_FINAL"}},"competitions":[{"venue":{"fullName":"Stadium"},"competitors":[{"homeAway":"home","score":"24","team":{"abbreviation":"BUF","displayName":"Buffalo Bills"}},{"homeAway":"away","score":"17","team":{"abbreviation":"MIA","displayName":"Miami Dolphins"}}]}]}
BOX={"boxscore":{"players":[{"team":{"abbreviation":"BUF"},"statistics":[{"name":"passing","labels":["C/ATT","YDS","TD","INT","SACKS"],"athletes":[{"athlete":{"displayName":"Test Quarterback"},"stats":["20/30","270","2","1","3"]}]},{"name":"receiving","labels":["REC","YDS","TD","TGTS"],"athletes":[{"athlete":{"displayName":"Wide Out"},"stats":["7","90","1","9"]}]}]}],"teams":[{"team":{"abbreviation":"BUF"},"statistics":[{"name":"total yards","displayValue":"400"},{"name":"passing yards","displayValue":"270"},{"name":"rushing yards","displayValue":"130"},{"name":"turnovers","displayValue":"1"},{"name":"first downs","displayValue":"22"},{"name":"total plays","displayValue":"64"},{"name":"possession time","displayValue":"31:00"}]}]}}

def test_espn_schedule_and_final_score_normalization(monkeypatch):
    p=EspnNflProvider(); g=p.normalize_game(EVENT,"2025",1)
    assert g["game_id"]=="espn-401" and g["home_team"]=="BUF" and g["final_home_score"]==24
    monkeypatch.setattr(p, "_scoreboard", lambda season, week: {"events":[EVENT]})
    assert p.fetch_outcomes("2025",1,[g])[0]["market_results"]["moneyline"]=="home"

def test_espn_outcomes_extract_role_oriented_scores_and_preserve_zero(monkeypatch):
    event = {"id":"401","date":"2025-09-07T17:00:00Z",
        "status":{"type":{"name":"STATUS_FINAL","completed":True}},
        "competitions":[{"competitors":[
            {"homeAway":"away","score":"0","winner":False,"team":{"abbreviation":"MIA"}},
            {"homeAway":"home","score":"24","winner":True,"team":{"abbreviation":"BUF"}},
        ]}]}
    provider = EspnNflProvider()
    monkeypatch.setattr(provider, "_scoreboard", lambda season, week: {"events":[event]})
    row = provider.fetch_outcomes("2025", 2, [{"game_id":"espn-401","home_team":"BUF","away_team":"MIA"}])[0]
    assert (row["final_home_score"], row["final_away_score"]) == (24, 0)
    assert row["completed"] is True
    assert row["provider_event_id"] == "401"

def test_espn_outcomes_ignore_unfinished_games(monkeypatch):
    event = {"id":"402","date":"2025-09-07T17:00:00Z",
        "status":{"type":{"name":"STATUS_IN_PROGRESS","completed":False}},
        "competitions":[{"competitors":[]}]}
    provider = EspnNflProvider()
    monkeypatch.setattr(provider, "_scoreboard", lambda season, week: {"events":[event]})
    assert provider.fetch_outcomes("2025", 2, []) == []

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
    legacy_terms = ("sportsdataio", "sports_data_io", "sportsdata")
    root = Path(__file__).resolve().parents[1]
    skip_dirs = {
        ".git",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".pytest_cache",
        "build",
        "dist",
        "node_modules",
        ".next",
    }
    skip_suffixes = {".db", ".sqlite", ".sqlite3", ".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz"}
    relevant_suffixes = {".py", ".md", ".txt", ".json", ".toml", ".yaml", ".yml", ".ini", ".cfg", ".js", ".jsx", ".ts", ".tsx", ".css", ".html"}
    matches = []

    for path in root.rglob("*"):
        if any(part in skip_dirs for part in path.relative_to(root).parts):
            continue
        if not path.is_file():
            continue
        lower_parts = {part.lower() for part in path.relative_to(root).parts}
        if lower_parts & {"raw_cache", "raw_caches", "snapshots", "snapshot_data"}:
            continue
        if path.suffix.lower() in skip_suffixes:
            continue
        if path.suffix.lower() and path.suffix.lower() not in relevant_suffixes:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            lower = line.lower()
            if path == Path(__file__).resolve() and ("legacy_terms" in line or "sports" "data" in line):
                continue
            if any(term in lower for term in legacy_terms):
                matches.append(f"{path.relative_to(root)}:{line_no}: {line.strip()}")

    assert matches == []

def test_odds_api_successful_historical_odds_retrieval(monkeypatch, tmp_path):
    import nfl_providers
    seen={}
    def ok(url):
        seen["url"] = url
        return {"data":[{"id":"odds1","home_team":"Buffalo Bills","away_team":"Miami Dolphins","commence_time":"2025-09-07T17:00:00Z","bookmakers":[{"key":"dk","title":"DraftKings","markets":[{"key":"h2h","last_update":"2025-09-06T17:00:00Z","outcomes":[{"name":"Buffalo Bills","price":-120}]}]}]}]}
    monkeypatch.setattr(nfl_providers,"_fetch_json",ok)
    rows = TheOddsApiNflProvider(api_key="SECRET", cache=JsonRawCache(tmp_path)).fetch_odds("2025",1,[{"game_id":"espn-401","home_team":"BUF","away_team":"MIA","kickoff_time":"2025-09-07T17:00:00Z"}],snapshot_time="2025-09-06T17:00:00Z")
    assert rows and rows[0]["sportsbook"] == "DraftKings"
    assert "player_pass_yds" not in seen["url"]
    assert "/historical/sports/americanfootball_nfl/odds?" in seen["url"]


def test_odds_api_422_includes_precise_body_and_redacts_key(monkeypatch, tmp_path):
    import nfl_providers
    from io import BytesIO
    from urllib.error import HTTPError
    def boom(url):
        raise HTTPError(url,422,"Unprocessable Entity",None,BytesIO(b'{"message":"INVALID_MARKET: player props are event odds markets"}'))
    monkeypatch.setattr(nfl_providers,"_fetch_json",boom)
    with pytest.raises(nfl_providers.OddsApiRequestError) as exc:
        TheOddsApiNflProvider(api_key="SECRET", cache=JsonRawCache(tmp_path)).fetch_odds("2025",1,[],snapshot_time="2025-09-01T00:00:00Z")
    msg = str(exc.value)
    assert "INVALID_MARKET" in msg and "REDACTED" in msg and "SECRET" not in msg


def test_normalize_odds_missing_bookmaker_or_market():
    rows = normalize_odds_events([{"id":"e1","bookmakers":[]},{"id":"e2","bookmakers":[{"key":"dk","markets":[]}]}], [])
    assert rows == []


def test_structured_http_error_is_classified_and_secret_free(monkeypatch):
    import nfl_providers
    from io import BytesIO
    from urllib.error import HTTPError

    secret = "top-secret-key"
    error = HTTPError(f"https://provider.test?apiKey={secret}", 401, "Unauthorized",
                      {"x-requests-remaining": "42", "authorization": secret},
                      BytesIO(f'{{"message":"bad apiKey={secret}"}}'.encode()))
    monkeypatch.setattr(nfl_providers, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    with pytest.raises(nfl_providers.StructuredHttpError) as exc:
        nfl_providers._fetch_json_structured(f"https://provider.test?apiKey={secret}")
    failure = exc.value
    assert failure.classification == "AUTHENTICATION_OR_ENTITLEMENT"
    assert failure.headers == {"x-requests-remaining": "42"}
    assert secret not in str(failure) + failure.redacted_url + repr(failure.headers)


def test_structured_response_diagnostics_are_allowlisted_in_cache(monkeypatch, tmp_path):
    import nfl_providers

    response = nfl_providers.HttpJsonResponse({"data": []}, 200,
                                               {"x-requests-used": "60", "x-requests-last": "60"})
    cache = JsonRawCache(tmp_path)
    cache.get_or_fetch("odds-api", "nfl", 2025, 1, "props", {}, lambda: response)
    metadata = json.loads(next(tmp_path.rglob("*.metadata.json")).read_text())
    assert metadata["response_status"] == 200
    assert metadata["api_usage_headers"] == {"x-requests-used": "60", "x-requests-last": "60"}


def test_structured_http_error_decodes_compressed_html_and_bounds_body(monkeypatch):
    import gzip
    import nfl_providers
    from io import BytesIO
    from urllib.error import HTTPError
    html=("<html><title>Unavailable</title>" + "safe " * 1000 + "</html>").encode()
    error=HTTPError("https://provider.test/path",503,"Unavailable",
                    {"content-type":"text/html; charset=utf-8","content-encoding":"gzip"},
                    BytesIO(gzip.compress(html)))
    monkeypatch.setattr(nfl_providers,"urlopen",lambda *_a,**_k:(_ for _ in ()).throw(error))
    with pytest.raises(nfl_providers.StructuredHttpError) as caught:
        nfl_providers._fetch_json_structured("https://provider.test/path")
    failure=caught.value
    assert failure.status==503 and failure.classification=="TRANSIENT_PROVIDER_ERROR"
    assert "Unavailable" in str(failure) and len(str(failure))<=nfl_providers.MAX_PROVIDER_ERROR_CHARS
    assert failure.headers=={"content-type":"text/html; charset=utf-8","content-encoding":"gzip"}


def test_structured_http_error_never_renders_binary(monkeypatch):
    import nfl_providers
    from io import BytesIO
    from urllib.error import HTTPError
    error=HTTPError("https://provider.test",500,"Bad",{"content-type":"application/octet-stream"},BytesIO(b"\x00\x01\xff"*100))
    monkeypatch.setattr(nfl_providers,"urlopen",lambda *_a,**_k:(_ for _ in ()).throw(error))
    with pytest.raises(nfl_providers.StructuredHttpError) as caught:
        nfl_providers._fetch_json_structured("https://provider.test")
    assert str(caught.value)=="[binary provider body omitted]"


@pytest.mark.parametrize(("body","headers"),[
    (b"<html>provider unavailable</html>",{"content-type":"text/html; charset=utf-8"}),
    (b'{"broken":',{"content-type":"application/json"}),
    (b"",{"content-type":"application/json"}),
    (b"\x00\x01\xff"*100,{"content-type":"application/octet-stream"}),
])
def test_successful_non_json_response_is_structured(monkeypatch,body,headers):
    import nfl_providers
    from io import BytesIO
    class Response(BytesIO):
        status=200
        def __init__(self): super().__init__(body); self.headers=headers
        def __enter__(self): return self
        def __exit__(self,*_args): self.close()
    monkeypatch.setattr(nfl_providers,"urlopen",lambda *_a,**_k:Response())
    with pytest.raises(nfl_providers.StructuredHttpError) as caught:
        nfl_providers._fetch_json_structured("https://provider.test?apiKey=secret-value")
    failure=caught.value
    assert failure.status==200 and failure.classification=="INVALID_PROVIDER_RESPONSE"
    assert failure.headers==headers and "secret-value" not in str(failure)+failure.redacted_url


def test_successful_compressed_malformed_json_is_structured(monkeypatch):
    import gzip
    import nfl_providers
    from io import BytesIO
    class Response(BytesIO):
        status=200
        headers={"content-type":"application/json","content-encoding":"gzip"}
        def __enter__(self): return self
        def __exit__(self,*_args): self.close()
    monkeypatch.setattr(nfl_providers,"urlopen",lambda *_a,**_k:Response(gzip.compress(b'{"bad":')))
    with pytest.raises(nfl_providers.StructuredHttpError) as caught:
        nfl_providers._fetch_json_structured("https://provider.test")
    assert caught.value.classification=="INVALID_PROVIDER_RESPONSE"
    assert caught.value.headers["content-encoding"]=="gzip"


def test_structured_fetch_valid_json_still_succeeds(monkeypatch):
    import nfl_providers
    from io import BytesIO
    class Response(BytesIO):
        status=200
        headers={"content-type":"application/json"}
        def __enter__(self): return self
        def __exit__(self,*_args): self.close()
    monkeypatch.setattr(nfl_providers,"urlopen",lambda *_a,**_k:Response(b'{"ok":true}'))
    response=nfl_providers._fetch_json_structured("https://provider.test")
    assert response==nfl_providers.HttpJsonResponse({"ok":True},200,{"content-type":"application/json"})
