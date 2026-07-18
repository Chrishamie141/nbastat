from __future__ import annotations
import os, time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from backend.app.schemas.common import SportsModeResponse
from backend.app.services.schedule_service import upcoming_games
_CACHE={}
VALID={'nfl','nba','both','offseason'}

def _calendar_active(league, now):
    lead=int(os.getenv(f'{league.upper()}_ACTIVATION_LEAD_DAYS', '21' if league=='nfl' else '14'))
    y=now.year
    tz=ZoneInfo(os.getenv('SPORTS_MODE_TIMEZONE','America/New_York'))
    if league=='nfl': starts=[datetime(y,8,1,tzinfo=tz), datetime(y,9,1,tzinfo=tz)]
    else: starts=[datetime(y,10,1,tzinfo=tz)]
    return any(0 <= (s-now).days <= lead for s in starts) or (league=='nfl' and now.month in [8,9,10,11,12,1]) or (league=='nba' and now.month in [10,11,12,1,2,3,4,5,6])

def _phase(league, active, now):
    if not active: return 'offseason'
    if league=='nfl' and now.month in [7,8]: return 'preseason'
    if league=='nba' and now.month==10: return 'preseason'
    if league=='nba' and now.month in [5,6]: return 'postseason'
    return 'regular_season'

def get_sports_mode(now=None):
    now=now or datetime.now(ZoneInfo(os.getenv('SPORTS_MODE_TIMEZONE','America/New_York')))
    override=os.getenv('SPORTS_MODE_OVERRIDE','auto').lower()
    if override in VALID:
        active=[] if override=='offseason' else (['nfl','nba'] if override=='both' else [override])
        return SportsModeResponse(mode=override,activeLeagues=active,phaseByLeague={l:_phase(l,l in active,now) for l in ['nfl','nba']},reason='Administrative sports mode override is active.',effectiveAt=datetime.now(timezone.utc),source='manual_override',overrideActive=True)
    key=now.strftime('%Y-%m-%d-%H')
    if key in _CACHE and _CACHE[key]['exp']>time.time(): return _CACHE[key]['data']
    source='fallback'; active=[]
    try:
        look=int(os.getenv('SCHEDULE_LOOKAHEAD_DAYS','30'))
        games=upcoming_games(['nfl','nba'], limit=50, start=datetime.now(timezone.utc)-timedelta(days=2), end=datetime.now(timezone.utc)+timedelta(days=look))
        source='schedule'
        active=sorted(set(g.league for g in games if g.status not in {'postponed','cancelled'}))
    except Exception:
        active=[]
    if not active:
        active=[l for l in ['nfl','nba'] if _calendar_active(l, now)]; source='configured_calendar' if active else 'fallback'
    mode='both' if set(active)=={'nfl','nba'} else (active[0] if active else 'offseason')
    phases={l:_phase(l,l in active,now) for l in ['nfl','nba']}
    reason = 'Schedule provider returned relevant upcoming games.' if source=='schedule' and active else ('Configured season windows activated supported leagues.' if active else 'No supported NFL or NBA games are active within the configured window.')
    resp=SportsModeResponse(mode=mode,activeLeagues=active,phaseByLeague=phases,reason=reason,effectiveAt=datetime.now(timezone.utc),source=source,overrideActive=False)
    _CACHE[key]={'data':resp,'exp':time.time()+900}; return resp
