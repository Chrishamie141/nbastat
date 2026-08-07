from __future__ import annotations
import time, os, requests, threading
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from backend.app.schemas.common import UpcomingGame, TeamSummary
from backend.app.services.game_watchability_service import score_game
from backend.app.services.team_metadata import team_by_abbreviation
from backend.app.services.game_status_service import normalize_game_status
from backend.app.services.game_status_service import lifecycle_cache_ttl

_CACHE={}
_REFRESH_LOCK=threading.Lock()
_LAST_MANUAL_REFRESH=0.0
ESPN={'nfl':'football/nfl','nba':'basketball/nba'}

def _phase(league, dt):
    m=dt.astimezone(ZoneInfo(os.getenv('SPORTS_MODE_TIMEZONE','America/New_York'))).month
    if league=='nfl': return 'preseason' if m==8 else ('regular_season' if m in [9,10,11,12,1] else 'offseason')
    return 'preseason' if m==10 else ('regular_season' if m in [10,11,12,1,2,3,4] else ('postseason' if m in [5,6] else 'offseason'))

def _provider_phase(value, league, dt):
    if isinstance(value,dict): value=value.get('slug') or value.get('name') or value.get('id') or value.get('type')
    if isinstance(value,int) or str(value).isdigit(): return {1:'preseason',2:'regular_season',3:'postseason'}.get(int(value),_phase(league,dt))
    text=str(value or '').lower().replace('-','_').replace(' ','_')
    if 'pre' in text: return 'preseason'
    if 'post' in text: return 'postseason'
    if 'regular' in text: return 'regular_season'
    return text or _phase(league,dt)

def _team(comp, league):
    t=comp.get('team') or {}; logos=t.get('logos') or []
    abbr=(t.get('abbreviation') or 'TBD').upper()
    meta=team_by_abbreviation(league, abbr)
    records=comp.get('records') or []
    record=next((row.get('summary') for row in records if row.get('type') in {'total','overall'} and row.get('summary')),None)
    record=record or next((row.get('summary') for row in records if row.get('summary')),None)
    if meta:
        data=meta.model_dump()
        data['id']=str(t.get('id') or data['id'])
        data['record']=record or data.get('record')
        if logos:
            data['logoUrl']=logos[0].get('href') or data['logoUrl']
        return TeamSummary(**data)
    name=t.get('displayName') or t.get('name') or abbr
    return TeamSummary(id=str(t.get('id') or abbr.lower()), league=league, name=name, city=None, nickname=None, abbreviation=abbr, logoUrl=(logos[0].get('href') if logos else None), record=record)

def _fetch_espn(league, start, end):
    dates=f"{start:%Y%m%d}-{end:%Y%m%d}"; url=f"https://site.api.espn.com/apis/site/v2/sports/{ESPN[league]}/scoreboard?dates={dates}&limit=100"
    r=requests.get(url,timeout=8); r.raise_for_status(); return r.json().get('events') or []

def _score(competitor):
    value=competitor.get('score')
    if isinstance(value, dict): value=value.get('value') or value.get('displayValue')
    try: return int(float(value))
    except (TypeError, ValueError): return None

def upcoming_games(leagues, limit=10, start=None, end=None, include_completed=False):
    now=datetime.now(timezone.utc); start=start or now; end=end or now+timedelta(days=int(os.getenv('SCHEDULE_LOOKAHEAD_DAYS','30')))
    key=(tuple(sorted(leagues)), limit, start.date().isoformat(), end.date().isoformat(), include_completed); cached=_CACHE.get(key)
    if cached and cached['exp']>time.time(): return cached['data']
    games=[]; provider='espn_scoreboard'; successful_fetches=0
    for lg in leagues:
        try:
            events=_fetch_espn(lg,start,end); successful_fetches+=1
        except Exception: continue
        for ev in events:
            comp=(ev.get('competitions') or [{}])[0]; comps=comp.get('competitors') or []
            if len(comps)<2: continue
            status_obj=comp.get('status') or ev.get('status') or {}; status_type=status_obj.get('type') or {}
            status=normalize_game_status(status_type.get('name'), status_type.get('detail') or status_type.get('shortDetail'), bool(status_type.get('completed')))
            if status in {'final','final-OT'} and not include_completed: continue
            dt=datetime.fromisoformat(ev.get('date').replace('Z','+00:00'))
            home=next((c for c in comps if c.get('homeAway')=='home'), comps[0]); away=next((c for c in comps if c.get('homeAway')=='away'), comps[-1])
            broadcasts=[b.get('names',[b.get('name')])[0] for b in comp.get('broadcasts',[]) if (b.get('names') or b.get('name'))]
            season=ev.get('season') or {}; week=ev.get('week') or comp.get('week') or {}; phase=season.get('slug') or season.get('type') or _phase(lg,dt)
            if isinstance(phase,int): phase={1:'preseason',2:'regular_season',3:'postseason'}.get(phase,_phase(lg,dt))
            phase=_provider_phase(phase,lg,dt)
            week_number=week.get('number') if isinstance(week,dict) else week
            address=(comp.get('venue') or {}).get('address') or {}
            d={'id':str(ev.get('id')), 'league':lg, 'seasonPhase':phase, 'season':season.get('year'), 'week':week_number, 'phaseWeekKey':f"{season.get('year') or dt.year}:{phase}:w{week_number or 0}", 'awayTeam':_team(away, lg), 'homeTeam':_team(home, lg), 'startTimeUtc':dt, 'status':status, 'statusDetail':status_type.get('detail') or status_type.get('shortDetail'), 'statusUpdatedAt':datetime.now(timezone.utc), 'awayScore':_score(away), 'homeScore':_score(home), 'venue':(comp.get('venue') or {}).get('fullName'), 'city':', '.join(filter(None,[address.get('city'),address.get('state')])), 'broadcast':broadcasts, 'nationalBroadcast':bool(broadcasts), 'dataProvider':provider, 'dataMode':'live'}
            d.update(score_game(d)); games.append(UpcomingGame(**d))
    if leagues and not successful_fetches:
        raise requests.RequestException('All configured schedule providers failed.')
    games=sorted(games,key=lambda g:g.startTimeUtc)[:limit]
    ttl=min((lifecycle_cache_ttl(game.status,game.startTimeUtc,now,stats_complete=(game.status not in {'final','final-OT'} or now-game.startTimeUtc>=timedelta(hours=24))) for game in games),default=300)
    _CACHE[key]={'data':games,'exp':time.time()+ttl}; return games

def refresh_games(leagues, limit=20, start=None, end=None, include_completed=True):
    """Debounced server-side schedule refresh; browsers never contact ESPN directly."""
    global _LAST_MANUAL_REFRESH
    now_dt=datetime.now(timezone.utc); start=start or now_dt-timedelta(days=7); end=end or now_dt+timedelta(days=int(os.getenv('SCHEDULE_LOOKAHEAD_DAYS','30')))
    key=(tuple(sorted(leagues)),limit,start.date().isoformat(),end.date().isoformat(),include_completed)
    debounce=int(os.getenv('NFL_MANUAL_REFRESH_DEBOUNCE_SECONDS','10'))
    with _REFRESH_LOCK:
        previous=_CACHE.get(key)
        if previous and time.time()-_LAST_MANUAL_REFRESH<debounce:
            return previous['data'],True
        _LAST_MANUAL_REFRESH=time.time(); _CACHE.pop(key,None)
        try:
            games=upcoming_games(leagues,limit=limit,start=start,end=end,include_completed=include_completed)
        except Exception:
            if previous: _CACHE[key]=previous
            raise
        if not games and previous and previous.get('data'):
            _CACHE[key]=previous
            return previous['data'],False
        return games,False
