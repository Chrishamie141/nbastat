"""Normalized NFL provider adapters for ESPN, The Odds API, optional NFL data, and composites."""
from __future__ import annotations

import hashlib, json, os, re, time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

NFL_SPORT_KEY = "americanfootball_nfl"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary"
REQUEST_TIMEOUT = 10
USER_AGENT = "SmartBetSports NFL provider/1.0"
PROP_MARKETS = ["player_pass_yds","player_rush_yds","player_reception_yds","player_receptions","player_anytime_td","player_pass_tds","player_pass_interceptions"]
TEAM_MARKETS = ["h2h", "spreads", "totals"]
MARKET_TO_STAT = {"player_pass_yds":"PASS_YDS","player_rush_yds":"RUSH_YDS","player_reception_yds":"REC_YDS","player_receptions":"RECEPTIONS","player_anytime_td":"TD","player_pass_tds":"PASS_TD","player_pass_interceptions":"PASS_INT"}
TEAM_ALIASES = {"ARI":"ARI","ARIZONA CARDINALS":"ARI","ATL":"ATL","ATLANTA FALCONS":"ATL","BAL":"BAL","BALTIMORE RAVENS":"BAL","BUF":"BUF","BUFFALO BILLS":"BUF","CAR":"CAR","CAROLINA PANTHERS":"CAR","CHI":"CHI","CHICAGO BEARS":"CHI","CIN":"CIN","CINCINNATI BENGALS":"CIN","CLE":"CLE","CLEVELAND BROWNS":"CLE","DAL":"DAL","DALLAS COWBOYS":"DAL","DEN":"DEN","DENVER BRONCOS":"DEN","DET":"DET","DETROIT LIONS":"DET","GB":"GB","GREEN BAY PACKERS":"GB","HOU":"HOU","HOUSTON TEXANS":"HOU","IND":"IND","INDIANAPOLIS COLTS":"IND","JAX":"JAX","JACKSONVILLE JAGUARS":"JAX","KC":"KC","KAN":"KC","KANSAS CITY CHIEFS":"KC","LAC":"LAC","LOS ANGELES CHARGERS":"LAC","LAR":"LAR","LOS ANGELES RAMS":"LAR","LV":"LV","LAS VEGAS RAIDERS":"LV","MIA":"MIA","MIAMI DOLPHINS":"MIA","MIN":"MIN","MINNESOTA VIKINGS":"MIN","NE":"NE","NEW ENGLAND PATRIOTS":"NE","NO":"NO","NEW ORLEANS SAINTS":"NO","NYG":"NYG","NEW YORK GIANTS":"NYG","NYJ":"NYJ","NEW YORK JETS":"NYJ","PHI":"PHI","PHILADELPHIA EAGLES":"PHI","PIT":"PIT","PITTSBURGH STEELERS":"PIT","SEA":"SEA","SEATTLE SEAHAWKS":"SEA","SF":"SF","SAN FRANCISCO 49ERS":"SF","TB":"TB","TAMPA BAY BUCCANEERS":"TB","TEN":"TEN","TENNESSEE TITANS":"TEN","WAS":"WAS","WSH":"WAS","WASHINGTON COMMANDERS":"WAS"}

class NflScheduleProvider(Protocol):
    def fetch_games(self, season: int|str, week: int) -> list[dict[str, Any]]: ...
class NflStatsProvider(Protocol):
    def fetch_player_stats(self, season: int|str, week: int, games: list[dict[str, Any]]) -> list[dict[str, Any]]: ...
    def fetch_team_stats(self, season: int|str, week: int, games: list[dict[str, Any]]) -> list[dict[str, Any]]: ...
class NflInjuryProvider(Protocol):
    def fetch_injuries(self, season: int|str, week: int, games: list[dict[str, Any]]) -> list[dict[str, Any]]: ...
class NflOddsProvider(Protocol):
    def fetch_odds(self, season: int|str, week: int, games: list[dict[str, Any]], snapshot_time: str|None=None) -> list[dict[str, Any]]: ...
class NflOutcomeProvider(Protocol):
    def fetch_outcomes(self, season: int|str, week: int, games: list[dict[str, Any]]) -> list[dict[str, Any]]: ...

class ProviderUnavailable(RuntimeError): pass
class HistoricalOddsUnavailable(ProviderUnavailable): pass

def normalize_team(value: Any) -> str:
    key = re.sub(r"[^A-Z0-9 ]", "", str(value or "").upper()).strip()
    return TEAM_ALIASES.get(key, key)

def _iso(value: Any) -> str|None:
    if not value: return None
    return str(value).replace("+00:00", "Z")

def _dt(value: str|None):
    if not value: return None
    try: return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError: return None

def match_events(espn_game: dict[str, Any], odds_event: dict[str, Any], tolerance_minutes: int=180) -> bool:
    if normalize_team(espn_game.get("home_team")) != normalize_team(odds_event.get("home_team")): return False
    if normalize_team(espn_game.get("away_team")) != normalize_team(odds_event.get("away_team")): return False
    a,b = _dt(espn_game.get("kickoff_time") or espn_game.get("commence_time")), _dt(odds_event.get("commence_time") or odds_event.get("kickoff_time"))
    return bool(a and b and abs((a-b).total_seconds()) <= tolerance_minutes*60)

@dataclass
class JsonRawCache:
    root: Path = Path("backtesting/data/raw_cache")
    overwrite: bool = False
    def get_or_fetch(self, provider: str, league: str, season: str|int, week: int, endpoint: str, params: dict[str, Any], fetcher):
        digest = hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()[:16]
        path = self.root/provider/league/str(season)/f"week_{int(week):02d}"/f"{endpoint}-{digest}.json"
        if path.exists() and not self.overwrite: return json.loads(path.read_text())
        data = fetcher(); path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(data, indent=2, sort_keys=True)+"\n"); return data

def _fetch_json(url: str, headers: dict[str,str]|None=None, timeout:int=REQUEST_TIMEOUT):
    req = Request(url, headers={"User-Agent":USER_AGENT, **(headers or {})})
    with urlopen(req, timeout=timeout) as r: return json.loads(r.read().decode())

class EspnNflProvider:
    name="espn"; supported_datasets={"games","player_stats","team_stats","outcomes","injuries"}
    def __init__(self, cache: JsonRawCache|None=None): self.cache=cache or JsonRawCache()
    def _scoreboard(self, season, week):
        params={"seasontype":2,"week":int(week),"dates":str(season)}; url=f"{ESPN_SCOREBOARD}?{urlencode(params)}"
        return self.cache.get_or_fetch(self.name,"nfl",season,week,"scoreboard",params,lambda:_fetch_json(url))
    def _summary(self, season, week, event_id):
        params={"event":event_id}; url=f"{ESPN_SUMMARY}?{urlencode(params)}"
        return self.cache.get_or_fetch(self.name,"nfl",season,week,"summary",params,lambda:_fetch_json(url))
    def fetch_games(self, season, week): return [self.normalize_game(e, season, week) for e in self._scoreboard(season,week).get("events",[]) if self.normalize_game(e,season,week).get("game_id")]
    def normalize_game(self,e,season,week):
        comps=((e or {}).get("competitions") or [{}])[0]; competitors=comps.get("competitors") or []
        home=next((c for c in competitors if c.get("homeAway")=="home"),{}); away=next((c for c in competitors if c.get("homeAway")=="away"),{})
        status=(e.get("status") or {}).get("type") or {}
        return {"game_id":f"espn-{e.get('id')}","espn_event_id":e.get("id"),"league":"nfl","season":str(season),"week":int(week),"kickoff_time":_iso(e.get("date")),"home_team":normalize_team((home.get("team") or {}).get("abbreviation") or (home.get("team") or {}).get("displayName")),"away_team":normalize_team((away.get("team") or {}).get("abbreviation") or (away.get("team") or {}).get("displayName")),"venue":(comps.get("venue") or {}).get("fullName"),"status":status.get("name") or status.get("state"),"final_home_score": int(home.get("score",0) or 0),"final_away_score": int(away.get("score",0) or 0)}
    def fetch_outcomes(self, season, week, games): return [{"game_id":g["game_id"],"final_home_score":g.get("final_home_score"),"final_away_score":g.get("final_away_score"),"player_results":{},"market_results":{"moneyline":"home" if (g.get("final_home_score") or 0)>(g.get("final_away_score") or 0) else "away"},"completed_at":g.get("kickoff_time")} for g in games if str(g.get("status","")).lower() in {"status_final","final","post"}]
    def fetch_player_stats(self, season, week, games):
        rows=[]
        for g in games:
            eid=str(g.get("espn_event_id") or str(g.get("game_id","")).replace("espn-","")); data=self._summary(season,week,eid)
            rows += normalize_espn_player_boxscore(data, str(season), int(week)-1)
        return rows
    def fetch_team_stats(self, season, week, games):
        rows=[]
        for g in games:
            eid=str(g.get("espn_event_id") or str(g.get("game_id","")).replace("espn-","")); rows += normalize_espn_team_boxscore(self._summary(season,week,eid), str(season), int(week)-1)
        return rows
    def fetch_injuries(self, season, week, games): return []

def normalize_espn_player_boxscore(data, season, through_week):
    out=[]
    for team in (data.get("boxscore",{}).get("players") or []):
        abbr=normalize_team((team.get("team") or {}).get("abbreviation"));
        for group in team.get("statistics",[]) or []:
            labels=[str(x).lower() for x in group.get("labels",[])]; name=str(group.get("name","")).lower()
            for ath in group.get("athletes",[]) or []:
                a=ath.get("athlete") or {}; vals=ath.get("stats") or []; stats={}
                for label,val in zip(labels, vals):
                    if label in {"cmp/att","c/att"} and "/" in str(val): stats.update({"completions": int(str(val).split('/')[0]), "attempts": int(str(val).split('/')[1])})
                    elif label in {"yds","yards"}: stats[("passing_yards" if "passing" in name else "rushing_yards" if "rushing" in name else "receiving_yards")]=_num(val)
                    elif label in {"td","tds"}: stats[("passing_touchdowns" if "passing" in name else "rushing_touchdowns" if "rushing" in name else "receiving_touchdowns")]=_num(val)
                    elif label in {"int"}: stats["interceptions"]=_num(val)
                    elif label in {"sacks"}: stats["sacks"]=_num(val)
                    elif label in {"car"}: stats["attempts"]=_num(val)
                    elif label in {"rec"}: stats["receptions"]=_num(val)
                    elif label in {"tgts","targets"}: stats["targets"]=_num(val)
                if stats: out.append({"player":a.get("displayName"),"team":abbr,"season":season,"through_week":through_week,"stats":stats})
    return out

def normalize_espn_team_boxscore(data, season, through_week):
    out=[]
    for t in (data.get("boxscore",{}).get("teams") or []):
        stats={}
        for s in t.get("statistics",[]) or []:
            k=str(s.get("name","")).lower().replace(" ","_"); v=s.get("displayValue", s.get("value"))
            if k in {"total_yards","passing_yards","rushing_yards","turnovers","first_downs","total_plays","possession_time"}: stats[{"total_plays":"plays","possession_time":"possession"}.get(k,k)] = v
        if stats: out.append({"team":normalize_team((t.get("team") or {}).get("abbreviation")),"season":season,"through_week":through_week,"stats":stats})
    return out

def _num(v):
    try: return int(v)
    except Exception:
        try: return float(v)
        except Exception: return None

class TheOddsApiNflProvider:
    name="odds-api"; supported_datasets={"odds"}
    def __init__(self, api_key=None, cache:JsonRawCache|None=None): self.api_key=api_key or os.getenv("THE_ODDS_API_KEY") or os.getenv("ODDS_API_KEY"); self.cache=cache or JsonRawCache()
    def fetch_odds(self, season, week, games, snapshot_time=None):
        if not self.api_key: raise ProviderUnavailable("THE_ODDS_API_KEY is not set")
        endpoint="historical/sports" if snapshot_time else "sports"; params={"apiKey":self.api_key,"regions":"us","markets":",".join(TEAM_MARKETS+PROP_MARKETS),"oddsFormat":"american"}
        if snapshot_time: params["date"]=snapshot_time
        url=f"{ODDS_API_BASE}/{endpoint}/{NFL_SPORT_KEY}/odds/?{urlencode(params)}"
        try: data=self.cache.get_or_fetch(self.name,"nfl",season,week,"odds",{k:v for k,v in params.items() if k!='apiKey'},lambda:_fetch_json(url))
        except HTTPError as e:
            if e.code in (401,402,403): raise HistoricalOddsUnavailable("The Odds API historical odds require an authorized subscription; current odds were not substituted")
            raise
        events=data.get("data",[]) if isinstance(data,dict) else data
        return normalize_odds_events(events, games)

def normalize_odds_events(events, games):
    rows=[]; captured=datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
    for ev in events or []:
        gid=next((g.get("game_id") for g in games if match_events(g,ev)), ev.get("id"))
        for book in ev.get("bookmakers",[]) or []:
            for market in book.get("markets",[]) or []:
                for o in market.get("outcomes",[]) or []:
                    rows.append({"game_id":gid,"event_id":ev.get("id"),"commence_time":ev.get("commence_time"),"market":MARKET_TO_STAT.get(market.get("key"), market.get("key")),"selection":o.get("description") or o.get("name"),"player":o.get("description"),"line":o.get("point"),"odds":o.get("price"),"sportsbook":book.get("title") or book.get("key"),"bookmaker":book.get("key"),"captured_at":market.get("last_update") or captured,"provider":"the-odds-api"})
    return rows

class NflOfficialProvider:
    name="nfl-official"; supported_datasets:set[str]=set(); disabled_reason="No dependable supported NFL-hosted JSON endpoint is configured; ESPN remains primary."
    def __getattr__(self,name):
        if name.startswith("fetch_"): return lambda *a,**k: (_ for _ in ()).throw(ProviderUnavailable(self.disabled_reason))
        raise AttributeError(name)

class CompositeNflProvider:
    def __init__(self, providers): self.providers=providers
    def _first(self, method, *args):
        for p in self.providers:
            fn=getattr(p, method, None)
            if fn:
                try:
                    rows=fn(*args)
                    if rows: return rows
                except Exception: continue
        return []
    def fetch_games(self, season, week): return self._first("fetch_games", season, week)
    def fetch_odds(self, season, week, games, snapshot_time=None): return self._first("fetch_odds", season, week, games, snapshot_time)
    def fetch_outcomes(self, season, week, games): return self._first("fetch_outcomes", season, week, games)
    def fetch_player_stats(self, season, week, games): return self._first("fetch_player_stats", season, week, games)
    def fetch_team_stats(self, season, week, games): return self._first("fetch_team_stats", season, week, games)
    def fetch_injuries(self, season, week, games): return self._first("fetch_injuries", season, week, games)
