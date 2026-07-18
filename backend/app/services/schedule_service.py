from __future__ import annotations
import time, os, requests
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from backend.app.schemas.common import UpcomingGame, TeamSummary
from backend.app.services.game_watchability_service import score_game
from backend.app.services.team_metadata import team_by_abbreviation

_CACHE={}
ESPN={'nfl':'football/nfl','nba':'basketball/nba'}

def _phase(league, dt):
    m=dt.astimezone(ZoneInfo(os.getenv('SPORTS_MODE_TIMEZONE','America/New_York'))).month
    if league=='nfl': return 'preseason' if m==8 else ('regular_season' if m in [9,10,11,12,1] else 'offseason')
    return 'preseason' if m==10 else ('regular_season' if m in [10,11,12,1,2,3,4] else ('postseason' if m in [5,6] else 'offseason'))

def _team(comp, league):
    t=comp.get('team') or {}; logos=t.get('logos') or []
    abbr=(t.get('abbreviation') or 'TBD').upper()
    meta=team_by_abbreviation(league, abbr)
    if meta:
        data=meta.model_dump()
        data['id']=str(t.get('id') or data['id'])
        if logos:
            data['logoUrl']=logos[0].get('href') or data['logoUrl']
        return TeamSummary(**data)
    name=t.get('displayName') or t.get('name') or abbr
    return TeamSummary(id=str(t.get('id') or abbr.lower()), league=league, name=name, city=None, nickname=None, abbreviation=abbr, logoUrl=(logos[0].get('href') if logos else None), record=None)

def _fetch_espn(league, start, end):
    dates=f"{start:%Y%m%d}-{end:%Y%m%d}"; url=f"https://site.api.espn.com/apis/site/v2/sports/{ESPN[league]}/scoreboard?dates={dates}&limit=100"
    r=requests.get(url,timeout=8); r.raise_for_status(); return r.json().get('events') or []

def upcoming_games(leagues, limit=10, start=None, end=None):
    now=datetime.now(timezone.utc); start=start or now; end=end or now+timedelta(days=int(os.getenv('SCHEDULE_LOOKAHEAD_DAYS','30')))
    key=(tuple(sorted(leagues)), limit, start.date().isoformat(), end.date().isoformat()); cached=_CACHE.get(key)
    if cached and cached['exp']>time.time(): return cached['data']
    games=[]; provider='espn_scoreboard'
    for lg in leagues:
        try: events=_fetch_espn(lg,start,end)
        except Exception: continue
        for ev in events:
            comp=(ev.get('competitions') or [{}])[0]; comps=comp.get('competitors') or []
            if len(comps)<2: continue
            status=(comp.get('status') or ev.get('status') or {}).get('type',{}).get('name','scheduled').lower()
            if status in {'status_final','final','completed'}: continue
            dt=datetime.fromisoformat(ev.get('date').replace('Z','+00:00'))
            home=next((c for c in comps if c.get('homeAway')=='home'), comps[0]); away=next((c for c in comps if c.get('homeAway')=='away'), comps[-1])
            broadcasts=[b.get('names',[b.get('name')])[0] for b in comp.get('broadcasts',[]) if (b.get('names') or b.get('name'))]
            d={'id':str(ev.get('id')), 'league':lg, 'seasonPhase':_phase(lg,dt), 'awayTeam':_team(away, lg), 'homeTeam':_team(home, lg), 'startTimeUtc':dt, 'status':'scheduled' if 'pre' in status or 'scheduled' in status else status, 'venue':(comp.get('venue') or {}).get('fullName'), 'broadcast':broadcasts, 'nationalBroadcast':bool(broadcasts), 'dataProvider':provider, 'dataMode':'live'}
            d.update(score_game(d)); games.append(UpcomingGame(**d))
    games=sorted(games,key=lambda g:g.startTimeUtc)[:limit]
    _CACHE[key]={'data':games,'exp':time.time()+900}; return games
