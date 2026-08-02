"""Normalized NFL provider adapters for ESPN, The Odds API, optional NFL data, and composites."""
from __future__ import annotations

import gzip, hashlib, json, os, re, time, zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from backtesting.game_matching import match_game, normalize_team, parse_dt as _dt
from backtesting.markets import ODDS_API_MARKET_ALIASES, ODDS_API_TEAM_MARKETS, normalize_market

NFL_SPORT_KEY = "americanfootball_nfl"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary"
ESPN_ROSTER = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams"
REQUEST_TIMEOUT = 10
USER_AGENT = "SmartBetSports NFL provider/1.0"
PROP_MARKETS = ["player_pass_yds","player_rush_yds","player_reception_yds","player_receptions","player_anytime_td","player_pass_tds","player_pass_interceptions"]
TEAM_MARKETS = list(ODDS_API_TEAM_MARKETS)
MARKET_TO_STAT = {key: market.value for key, market in ODDS_API_MARKET_ALIASES.items()}

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

class OddsApiRequestError(HistoricalOddsUnavailable):
    """The Odds API rejected a historical odds request with a provider-supplied reason."""


USAGE_HEADER_NAMES = ("x-requests-remaining", "x-requests-used", "x-requests-last", "retry-after")
DIAGNOSTIC_HEADER_NAMES = USAGE_HEADER_NAMES + ("content-type", "content-encoding")
MAX_PROVIDER_ERROR_CHARS = 2048


@dataclass(frozen=True)
class HttpJsonResponse:
    """A parsed HTTP response with a deliberately small, non-secret header set."""
    payload: Any
    status: int
    headers: dict[str, str]


class StructuredHttpError(RuntimeError):
    """Sanitized provider failure suitable for reports (never contains a credential)."""
    def __init__(self, *, status: int | None, message: str, classification: str,
                 url: str, headers: dict[str, str] | None = None):
        message = _sanitize_provider_text(message, url)
        super().__init__(message)
        self.status, self.provider_message = status, message
        self.classification, self.redacted_url = classification, _redact_url(url)
        self.headers = headers or {}


def _iso(value: Any) -> str|None:
    if not value: return None
    return str(value).replace("+00:00", "Z")

def match_events(espn_game: dict[str, Any], odds_event: dict[str, Any], tolerance_minutes: int=180) -> bool:
    return match_game(odds_event, [espn_game], tolerance_minutes=tolerance_minutes).matched

@dataclass
class JsonRawCache:
    root: Path = Path("backtesting/data/raw_cache")
    overwrite: bool = False
    hits: int = 0
    misses: int = 0
    @staticmethod
    def identity(provider: str, league: str, season: str|int, week: int,
                 endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        """Return the complete, secret-free identity of a provider response."""
        return {"provider": provider, "sport": league.lower(), "season": str(season),
                "week": int(week), "endpoint": endpoint,
                "params": {k: v for k, v in params.items() if k.lower() not in {"apikey", "api_key"}}}

    def path(self, provider: str, league: str, season: str|int, week: int,
             endpoint: str, params: dict[str, Any]) -> Path:
        identity = self.identity(provider, league, season, week, endpoint, params)
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode()).hexdigest()[:16]
        return self.root/provider/league.lower()/str(season)/f"week_{int(week):02d}"/f"{endpoint}-{digest}.json"

    def get_or_fetch(self, provider: str, league: str, season: str|int, week: int,
                     endpoint: str, params: dict[str, Any], fetcher, *,
                     overwrite: bool = False, replacement_reason: str | None = None):
        params = {k: v for k, v in params.items() if k.lower() not in {"apikey", "api_key"}}
        path = self.path(provider, league, season, week, endpoint, params)
        replace = overwrite or self.overwrite
        if path.exists() and not replace:
            self.hits += 1
            return json.loads(path.read_text())
        invalidated = None
        if path.exists() and replace:
            # Preserve the exact response that caused validation to fail.  The
            # quarantine name is deterministic for this replacement attempt and
            # contains no request credentials.
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            invalidated = path.with_name(f"{path.stem}.invalid-{stamp}{path.suffix}")
            path.rename(invalidated)
            old_meta = path.with_suffix(".metadata.json")
            if old_meta.exists():
                old_meta.rename(invalidated.with_suffix(".metadata.json"))
        self.misses += 1
        fetched = fetcher()
        diagnostics = {}
        if isinstance(fetched, HttpJsonResponse):
            data = fetched.payload
            diagnostics = {"response_status": fetched.status, "api_usage_headers": fetched.headers}
        else:
            data = fetched
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(data, indent=2, sort_keys=True)+"\n").encode()
        path.write_bytes(payload)
        events = data.get("data", []) if isinstance(data, dict) else data
        meta = {"request_timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "requested_historical_date": params.get("date"), "provider": provider, "sport": league,
            "season": str(season), "week": int(week), "endpoint": endpoint,
            "request_identity": self.identity(provider, league, season, week, endpoint, params),
            "markets": str(params.get("markets", "")).split(",") if params.get("markets") else [],
            "event_count": len(events) if isinstance(events, list) else 0,
            "response_sha256": hashlib.sha256(payload).hexdigest(), "api_usage_headers": diagnostics.get("api_usage_headers", {}),
            "response_status": diagnostics.get("response_status"),
            "previous_cache_invalidated": bool(invalidated),
            "replacement_fetched": bool(invalidated),
            "replacement_reason": replacement_reason,
            "quarantined_cache_path": str(invalidated) if invalidated else None}
        path.with_suffix(".metadata.json").write_text(json.dumps(meta, indent=2, sort_keys=True)+"\n")
        return data

def _fetch_json(url: str, headers: dict[str,str]|None=None, timeout:int=REQUEST_TIMEOUT):
    req = Request(url, headers={"User-Agent":USER_AGENT, **(headers or {})})
    with urlopen(req, timeout=timeout) as r: return json.loads(r.read().decode())


def _safe_response_headers(headers: Any) -> dict[str, str]:
    """Copy only non-secret diagnostics; authorization headers cannot escape."""
    if not headers:
        return {}
    return {name: str(value) for name in DIAGNOSTIC_HEADER_NAMES
            if (value := headers.get(name)) is not None}


def _classify_http_status(status: int) -> str:
    if status in (401, 403): return "AUTHENTICATION_OR_ENTITLEMENT"
    if status == 429: return "RATE_LIMITED"
    if 500 <= status <= 599: return "TRANSIENT_PROVIDER_ERROR"
    return "REQUEST_ERROR"


def _fetch_json_structured(url: str, headers: dict[str,str]|None=None,
                           timeout:int=REQUEST_TIMEOUT) -> HttpJsonResponse:
    """Fetch JSON while retaining safe diagnostics and sanitizing all failures."""
    req = Request(url, headers={"User-Agent":USER_AGENT, **(headers or {})})
    try:
        with urlopen(req, timeout=timeout) as response:
            body = _decode_http_body(response.read(), response.headers)
            status=int(getattr(response, "status", 200))
            safe_headers=_safe_response_headers(response.headers)
            try:
                payload=json.loads(body)
            except (json.JSONDecodeError, TypeError, ValueError):
                preview=body[:MAX_PROVIDER_ERROR_CHARS] if body else "[empty provider body]"
                raise StructuredHttpError(status=status, message=preview,
                    classification="INVALID_PROVIDER_RESPONSE", url=url,
                    headers=safe_headers) from None
            return HttpJsonResponse(payload,status,safe_headers)
    except HTTPError as error:
        raise StructuredHttpError(status=error.code, message=_read_http_error(error),
                                  classification=_classify_http_status(error.code), url=url,
                                  headers=_safe_response_headers(error.headers)) from None
    except (URLError, TimeoutError) as error:
        reason = getattr(error, "reason", error)
        raise StructuredHttpError(status=None, message=str(reason), classification="NETWORK_ERROR",
                                  url=url) from None

def _read_http_error(e: HTTPError) -> str:
    try:
        body = _decode_http_body(e.read(MAX_PROVIDER_ERROR_CHARS * 8 + 1), e.headers)
    except Exception:
        body = ""
    try:
        parsed = json.loads(body) if body else {}
        detail = parsed.get("message") or parsed.get("error") or parsed.get("detail") or body
    except Exception:
        detail = body
    return _sanitize_provider_text(str(detail or e.reason or f"HTTP {e.code}"))[:MAX_PROVIDER_ERROR_CHARS]


def _decode_http_body(raw: bytes, headers: Any) -> str:
    """Decode a bounded provider body without ever rendering arbitrary bytes."""
    encoding = str(headers.get("content-encoding", "") if headers else "").lower().strip()
    try:
        if encoding == "gzip": raw = gzip.decompress(raw)
        elif encoding == "deflate":
            try: raw = zlib.decompress(raw)
            except zlib.error: raw = zlib.decompress(raw, -zlib.MAX_WBITS)
        elif encoding == "br":
            try:
                import brotli
                raw = brotli.decompress(raw)
            except Exception:
                return "[compressed provider body omitted]"
        elif encoding and encoding != "identity":
            return "[encoded provider body omitted]"
    except (OSError, EOFError, zlib.error):
        return "[invalid compressed provider body omitted]"
    content_type = str(headers.get("content-type", "") if headers else "")
    charset_match = re.search(r"charset\s*=\s*[\"']?([^;\s\"']+)", content_type, re.I)
    charset = charset_match.group(1) if charset_match else "utf-8"
    try: text = raw.decode(charset, "replace")
    except LookupError: text = raw.decode("utf-8", "replace")
    # Replacement/control-heavy output is binary, not useful diagnostics.
    controls = sum(ord(c) < 32 and c not in "\r\n\t" for c in text)
    if "\ufffd" in text or controls > max(2, len(text) // 20):
        return "[binary provider body omitted]"
    # Successful JSON responses must be decoded in full before parsing.  The
    # caller bounds malformed/error previews separately; truncating here turns
    # every valid provider payload larger than the diagnostic limit into
    # invalid JSON and can waste paid requests before the cache is written.
    return text


def _sanitize_provider_text(value: str, request_url: str = "") -> str:
    """Redact credential-shaped query fields and the request's credential value."""
    text = re.sub(r"((?:apiKey|api_key|apikey)\s*[=:]\s*)[^&\s\"']+", r"\1REDACTED",
                  str(value), flags=re.I)
    text = re.sub(r"((?:authorization|bearer|token|access_token|client_secret)\s*[=:]\s*)[^&\s\"']+",
                  r"\1REDACTED", text, flags=re.I)
    match = re.search(r"(?:apiKey|api_key|apikey)=([^&]+)", request_url, flags=re.I)
    if match and match.group(1):
        text = text.replace(match.group(1), "REDACTED")
    return text

def _redact_url(url: str) -> str:
    return re.sub(r"((?:apiKey|api_key|apikey|token|access_token|client_secret)=)[^&]+",
                  r"\1REDACTED", url, flags=re.I)

class EspnNflProvider:
    name="espn"; supported_datasets={"games","player_stats","team_stats","outcomes","injuries"}
    def __init__(self, cache: JsonRawCache|None=None, *, allow_network: bool=True):
        self.cache=cache or JsonRawCache(); self.allow_network=allow_network
    def _cached_json(self, season, week, endpoint, params, fetcher):
        path=self.cache.path(self.name,"nfl",season,week,endpoint,params)
        if not self.allow_network and not path.exists():
            raise ProviderUnavailable(f"cache-only ESPN {endpoint} missing: {path}")
        return self.cache.get_or_fetch(self.name,"nfl",season,week,endpoint,params,fetcher)
    def _scoreboard(self, season, week):
        params={"seasontype":2,"week":int(week),"dates":str(season)}; url=f"{ESPN_SCOREBOARD}?{urlencode(params)}"
        return self._cached_json(season,week,"scoreboard",params,lambda:_fetch_json(url))
    def _summary(self, season, week, event_id):
        params={"event":event_id}; url=f"{ESPN_SUMMARY}?{urlencode(params)}"
        return self._cached_json(season,week,"summary",params,lambda:_fetch_json(url))
    def fetch_games(self, season, week): return [self.normalize_game(e, season, week) for e in self._scoreboard(season,week).get("events",[]) if self.normalize_game(e,season,week).get("game_id")]
    def normalize_game(self,e,season,week):
        comps=((e or {}).get("competitions") or [{}])[0]; competitors=comps.get("competitors") or []
        home=next((c for c in competitors if c.get("homeAway")=="home"),{}); away=next((c for c in competitors if c.get("homeAway")=="away"),{})
        status=(e.get("status") or {}).get("type") or {}
        return {"game_id":f"espn-{e.get('id')}","espn_event_id":e.get("id"),"league":"nfl","season":str(season),"week":int(week),"kickoff_time":_iso(e.get("date")),"home_team":normalize_team((home.get("team") or {}).get("abbreviation") or (home.get("team") or {}).get("displayName")),"away_team":normalize_team((away.get("team") or {}).get("abbreviation") or (away.get("team") or {}).get("displayName")),"venue":(comps.get("venue") or {}).get("fullName"),"status":status.get("name") or status.get("state"),"final_home_score": int(home.get("score",0) or 0),"final_away_score": int(away.get("score",0) or 0),"source":"espn","captured_at":_iso(e.get("date")),"data_as_of":_iso(e.get("date")),"is_pregame":True}
    def fetch_outcomes(self, season, week, games):
        """Extract finals from the scoreboard, not the lossy schedule rows.

        Persisted ``games.json`` deliberately has a schedule schema and older
        snapshots therefore do not carry scores.  The ESPN scoreboard is the
        outcome source of truth; competitor roles, rather than array order,
        orient its scores.
        """
        canonical = {}
        for game in games:
            event_id = str(game.get("espn_event_id") or str(game.get("game_id", "")).removeprefix("espn-"))
            canonical[event_id] = game
        rows=[]
        for event in self._scoreboard(season, week).get("events", []):
            status = ((event.get("status") or {}).get("type") or {})
            is_final = bool(status.get("completed")) or str(status.get("name") or status.get("state") or "").lower() in {"status_final", "final", "post"}
            if not is_final:
                continue
            competitors = (((event.get("competitions") or [{}])[0]).get("competitors") or [])
            by_role = {c.get("homeAway"): c for c in competitors if c.get("homeAway") in {"home", "away"}}
            try:
                home_score, away_score = int(by_role["home"]["score"]), int(by_role["away"]["score"])
            except (KeyError, TypeError, ValueError):
                # A final without two parseable role-oriented scores is not a
                # gradeable outcome and must not be fabricated.
                continue
            event_id = str(event.get("id"))
            game = canonical.get(event_id, {})
            home = normalize_team((by_role["home"].get("team") or {}).get("abbreviation") or (by_role["home"].get("team") or {}).get("displayName"))
            away = normalize_team((by_role["away"].get("team") or {}).get("abbreviation") or (by_role["away"].get("team") or {}).get("displayName"))
            completed_at = _iso(event.get("date"))
            winner = "home" if home_score > away_score else "away" if away_score > home_score else "tie"
            rows.append({"league":"nfl","season":str(season),"week":int(week),
                "game_id":game.get("game_id") or f"espn-{event_id}", "provider_event_id":event_id,
                "home_team":game.get("home_team") or home,"away_team":game.get("away_team") or away,
                "final_home_score":home_score,"final_away_score":away_score,"completed":True,
                "status":status.get("name") or status.get("state"),"completed_at":completed_at,
                "captured_at":completed_at,"data_as_of":completed_at,"is_pregame":False,
                "source":"espn-scoreboard","player_results":{},"market_results":{"moneyline":winner}})
        return rows
    def fetch_player_stats(self, season, week, games):
        rows=[]
        for g in games:
            eid=str(g.get("espn_event_id") or str(g.get("game_id","")).replace("espn-","")); data=self._summary(season,week,eid)
            
            for r in normalize_espn_player_boxscore(data, str(season), int(week)-1):
                r.update({"game_id": g.get("game_id"), "source":"espn", "captured_at": g.get("kickoff_time"), "data_as_of": g.get("kickoff_time"), "is_pregame": True, "record_role":"pregame_history", "week": int(week)})
                rows.append(r)
        return rows
    def fetch_team_stats(self, season, week, games):
        """Return completed-game observations known before this snapshot's games.

        Unlike the old implementation, this never reads the target game's
        boxscore.  Week one therefore uses the preceding regular season; later
        weeks additionally use already completed games in the target season.
        """
        target_season, target_week = int(season), int(week)
        earliest_kickoff = min((_dt(g.get("kickoff_time")) for g in games if g.get("kickoff_time")), default=None)
        rows=[]
        for history_season, weeks in ((target_season - 1, range(1, 19)), (target_season, range(1, target_week))):
            for history_week in weeks:
                payload = self._scoreboard(history_season, history_week)
                for event in payload.get("events", []):
                    status = ((event.get("status") or {}).get("type") or {})
                    if not status.get("completed"):
                        continue
                    game = self.normalize_game(event, history_season, history_week)
                    kickoff = _dt(game.get("kickoff_time"))
                    # ESPN's public scoreboard has no finalization instant. Six
                    # hours after kickoff is a conservative deterministic lower
                    # bound for when a final score was knowable.
                    completed = kickoff + timedelta(hours=6) if kickoff else None
                    if not completed or (earliest_kickoff and completed >= earliest_kickoff):
                        continue
                    completed_at = completed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
                    competitors = (((event.get("competitions") or [{}])[0]).get("competitors") or [])
                    if len(competitors) != 2:
                        continue
                    normalized=[]
                    for competitor in competitors:
                        team = normalize_team((competitor.get("team") or {}).get("abbreviation") or (competitor.get("team") or {}).get("displayName"))
                        try: score = int(competitor.get("score"))
                        except (TypeError, ValueError): break
                        normalized.append((competitor.get("homeAway"), team, score))
                    if len(normalized) != 2:
                        continue
                    for home_away, team, score in normalized:
                        opponent = next(item for item in normalized if item[1] != team)
                        rows.append({"season":history_season,"week":history_week,"through_week":history_week,
                            "team":team,"game_id":game.get("game_id"),"opponent":opponent[1],
                            "points_for":score,"points_against":opponent[2],"home_away":home_away,
                            "completed_at":completed_at,"captured_at":completed_at,"data_as_of":completed_at,
                            "record_role":"completed_game_history","source":"espn-scoreboard","is_pregame":False})
        identity=lambda r:(r["season"],r["week"],r["game_id"],r["team"])
        return sorted({identity(r):r for r in rows}.values(), key=identity)
    def fetch_injuries(self, season, week, games): return []

def normalize_espn_player_boxscore(data, season, through_week, diagnostics=None):
    """Normalize ESPN ``boxscore.players[].statistics[].athletes[]`` rows.

    Rows intentionally remain category-split.  An absent value stays absent,
    while a provider-supplied ``"0"`` is retained as numeric zero.
    """
    out=[]
    inspected=offensive=missing_ids=rejected=0
    emitted={"passing":0,"rushing":0,"receiving":0}; reasons={}
    for team in (data.get("boxscore",{}).get("players") or []):
        abbr=normalize_team((team.get("team") or {}).get("abbreviation"));
        for group in team.get("statistics",[]) or []:
            labels=[str(x).strip().lower() for x in group.get("labels",[])]; name=str(group.get("name") or group.get("displayName") or "").strip().lower()
            category=next((kind for kind in emitted if kind in name),None)
            for ath in group.get("athletes",[]) or []:
                inspected += 1
                a=ath.get("athlete") or {}; vals=ath.get("stats") or []; stats={}
                for label,val in zip(labels, vals):
                    if label in {"cmp/att","c/att"} and "/" in str(val):
                        cmp_,att=str(val).split('/',1); stats.update({"completions":_num(cmp_),"passing_attempts":_num(att)})
                    elif label in {"yds","yards"} and category: stats[f"{category}_yards"]=_num(val)
                    elif label in {"td","tds"} and category: stats[f"{category}_tds"]=_num(val)
                    elif label in {"int"}: stats["interceptions"]=_num(val)
                    elif label in {"sacks"}: stats["sacks"]=_num(val)
                    elif label in {"car","carries","att"} and category == "rushing": stats["rushing_attempts"]=_num(val)
                    elif label in {"rec"}: stats["receptions"]=_num(val)
                    elif label in {"tgts","targets"}: stats["targets"]=_num(val)
                stats={key:value for key,value in stats.items() if value is not None}
                if category and stats: offensive += 1
                athlete_id=a.get("id") or a.get("uid")
                if not athlete_id: missing_ids += 1
                if stats and category:
                    emitted[category] += 1
                    out.append({"canonical_player_id":athlete_id,"player_id":athlete_id,
                    "athlete_id":athlete_id,"provider_player_id":athlete_id,
                    "player":a.get("displayName"),"player_name":a.get("displayName"),
                    "position":((a.get("position") or {}).get("abbreviation") if isinstance(a.get("position"),dict) else a.get("position")),
                    "team":abbr,"season":season,"through_week":through_week,"category":category,"stat_category":category,
                    "stats":stats})
                else:
                    rejected += 1; reason="unsupported_category" if not category else "no_supported_stats"
                    reasons[reason]=reasons.get(reason,0)+1
    if diagnostics is not None:
        diagnostics.update({"raw_player_stat_records_inspected":inspected,"offensive_players_discovered":offensive,
            "passing_rows_emitted":emitted["passing"],"rushing_rows_emitted":emitted["rushing"],
            "receiving_rows_emitted":emitted["receiving"],"rows_rejected":rejected,
            "rejection_reasons":reasons,"provider_ids_present":inspected-missing_ids,"provider_ids_missing":missing_ids})
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
    def fetch_odds(self, season, week, games, snapshot_time=None, *,
                   overwrite_cache=False, replacement_reason=None):
        endpoint="historical/sports" if snapshot_time else "sports"
        # The Odds API /odds endpoint supports game markets. NFL player props are event-level
        # markets and cause 422 INVALID_MARKET responses when mixed into this request.
        params={"apiKey":self.api_key,"regions":"us","markets":",".join(TEAM_MARKETS),"oddsFormat":"american"}
        if snapshot_time: params["date"]=snapshot_time
        url=f"{ODDS_API_BASE}/{endpoint}/{NFL_SPORT_KEY}/odds?{urlencode(params)}"
        def fetch():
            if not self.api_key:
                raise ProviderUnavailable("THE_ODDS_API_KEY is not set")
            return _fetch_json(url)
        cache_hits_before = self.cache.hits
        try: data=self.cache.get_or_fetch(self.name,"nfl",season,week,"odds",{k:v for k,v in params.items() if k!='apiKey'},fetch,
                                          overwrite=overwrite_cache,
                                          replacement_reason=replacement_reason)
        except HTTPError as e:
            detail = _read_http_error(e)
            safe_url = _redact_url(url)
            if e.code in (401,402,403):
                raise HistoricalOddsUnavailable(f"The Odds API historical odds require an authorized subscription ({e.code}: {detail}); current odds were not substituted") from e
            if e.code == 422:
                raise OddsApiRequestError(f"The Odds API rejected historical odds request (422: {detail}). url={safe_url}. Likely causes: invalid date, unsupported market for endpoint, unsupported event, or subscription limitation.") from e
            raise OddsApiRequestError(f"The Odds API odds request failed ({e.code}: {detail}). url={safe_url}") from e
        events=data.get("data",[]) if isinstance(data,dict) else data
        response_timestamp = data.get("timestamp") if isinstance(data, dict) else snapshot_time
        self.last_diagnostics = {"raw_cache_hit": self.cache.hits > cache_hits_before,
                                 "cache_replaced": bool(overwrite_cache),
                                 "requested_date": snapshot_time,
                                 "response_timestamp": response_timestamp}
        rows = normalize_odds_events(
            events, games, diagnostics=self.last_diagnostics,
            debug=os.getenv("BACKTESTING_ODDS_DEBUG", "").lower() in {"1", "true", "yes"},
        )
        for row in rows:
            row["snapshot_timestamp"] = response_timestamp or snapshot_time
            # A historical quote was knowable at the returned snapshot.  A
            # bookmaker's last update is provenance about the market, not the
            # time at which we obtained the historical snapshot.
            row["captured_at"] = response_timestamp or snapshot_time
            row["data_as_of"] = response_timestamp or snapshot_time
        return rows

def _event_row_count(event: dict[str, Any]) -> int:
    return sum(
        len(market.get("outcomes", []) or [])
        for book in event.get("bookmakers", []) or []
        for market in book.get("markets", []) or []
    )


def normalize_odds_events(events, games, *, diagnostics=None, debug=False):
    """Match provider events once, then propagate the canonical ID to their rows.

    Historical ``/odds`` responses can contain events outside the requested ESPN
    game.  Those events must not be flattened with their provider IDs because
    doing so turns one irrelevant event into thousands of invalid snapshot rows.
    """
    rows=[]; captured=datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
    unique_events: dict[str, dict[str, Any]] = {}
    matched_ids: set[str] = set()
    unmatched: list[tuple[dict[str, Any], Any, int]] = []
    for index, ev in enumerate(events or []):
        event_id = str(ev.get("id") or ev.get("event_id") or f"missing-id-{index}")
        # Provider payloads occasionally repeat an event. Reconciliation and
        # diagnostics are event-based rather than bookmaker-row-based.
        if event_id in unique_events:
            continue
        unique_events[event_id] = ev
        diag=match_game(ev, games, league="nfl")
        if not diag.matched:
            unmatched.append((ev, diag, _event_row_count(ev)))
            continue
        matched_ids.add(event_id)
        gid=diag.game_id
        for book in ev.get("bookmakers",[]) or []:
            for market in book.get("markets",[]) or []:
                for o in market.get("outcomes",[]) or []:
                    rows.append({"game_id":gid,"event_id":ev.get("id") or ev.get("event_id"),"provider_event_matched":True,"commence_time":ev.get("commence_time"),"home_team":ev.get("home_team"),"away_team":ev.get("away_team"),"league":ev.get("league") or ev.get("sport_key") or "nfl","market":normalize_market(market.get("key")),"selection":o.get("description") or o.get("name"),"player":o.get("description"),"line":(0 if normalize_market(market.get("key")) == "h2h" and o.get("point") is None else o.get("point")),"odds":int(o.get("price")) if o.get("price") is not None else None,"sportsbook":book.get("title") or book.get("key"),"bookmaker":book.get("key"),"market_last_update":market.get("last_update"),"captured_at":market.get("last_update") or captured,"provider":"the-odds-api","source":"the-odds-api-historical","data_as_of":market.get("last_update") or captured,"is_pregame":True})
    if diagnostics is not None:
        ambiguous = sum("ambiguous_match" in diag.reasons for _, diag, _ in unmatched)
        diagnostics.update({
            "provider_events_received": len(unique_events),
            "provider_events_matched": len(matched_ids),
            "provider_events_discarded": len(unmatched),
            "odds_rows_persisted": len(rows),
        })
        if ambiguous:
            diagnostics["provider_events_ambiguous"] = ambiguous
    for ev, diag, affected_rows in unmatched:
        if debug:
            print(
            "Unmatched Odds API event: "
            f"provider_event_id={ev.get('id') or ev.get('event_id')}; "
            f"home_team={ev.get('home_team')}; away_team={ev.get('away_team')}; "
            f"commence_time={ev.get('commence_time')}; "
            f"closest_espn_candidate={diag.closest_game_id} "
            f"({diag.closest_away_team} at {diag.closest_home_team}, {diag.closest_kickoff_time}); "
            f"reason={','.join(diag.reasons)}; affected_rows={affected_rows}"
            )
    if debug:
        print(
            "Odds reconciliation summary: "
            f"unique_events_received={len(unique_events)}; "
            f"unique_events_matched={len(matched_ids)}; "
            f"unique_events_unmatched={len(unmatched)}; "
            f"odds_rows_assigned={len(rows)}"
        )
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
