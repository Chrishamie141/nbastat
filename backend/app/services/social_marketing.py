"""Company-only official X API publishing with verified, deterministic claims."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from difflib import SequenceMatcher
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit

import requests
from database_safety import assert_postgres_allowed, assert_sqlite_target, PRODUCTION_DATABASE
from backend.app.services.social import opportunities as opportunity_service
from backend.app.services.social import scheduler as queue_scheduler
from backend.app.services.social import settings as social_settings
from backend.app.services.social.content import validate_caption
from backend.app.services.social.media.assets import storage_factory
from backend.app.services.social.media.service import generate_media
from backend.app.services.social.publisher import OfficialX
from backend.app.services.social.storage import decoded, initialize_schema

logger = logging.getLogger(__name__)
SOCIAL_SCHEMA_LOCK_ID = 734120


def now(): return datetime.now(timezone.utc)
def encode(value): return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
def sha(value): return hashlib.sha256(encode(value).encode()).hexdigest()
def enabled(name): return os.getenv(name,'').lower()=='true'


@contextmanager
def connection():
    url=os.getenv('SOCIAL_DATABASE_URL','')
    if not url and os.getenv('VERCEL'):
        url=os.getenv('POSTGRES_URL') or os.getenv('POSTGRES_URL_NON_POOLING','')
    if not url: raise ValueError('SOCIAL_DATABASE_URL is required; no production default')
    pg=url.startswith(('postgres://','postgresql://'))
    if os.getenv('VERCEL') and not pg: raise ValueError('Vercel requires durable PostgreSQL social storage')
    if pg:
        assert_postgres_allowed()
        from backend.app.database import _postgres_url
        import psycopg
        from psycopg.rows import dict_row
        raw=psycopg.connect(_postgres_url(url),row_factory=dict_row,connect_timeout=5,prepare_threshold=None)
    else:
        if not url.startswith('sqlite:///'): raise ValueError('Unsupported social storage URL')
        path=Path(url[len('sqlite:///'):]).resolve()
        if path.name.lower()=='predictions.db' or path==PRODUCTION_DATABASE.resolve() or (path.exists() and PRODUCTION_DATABASE.exists() and path.samefile(PRODUCTION_DATABASE)):
            raise ValueError('Social storage cannot use predictions.db')
        assert_sqlite_target(path)
        path.parent.mkdir(parents=True,exist_ok=True)
        raw=sqlite3.connect(path,timeout=30)
        raw.row_factory=sqlite3.Row
    class Adapter:
        postgres=pg
        def execute(self,sql,values=()): return raw.execute(sql.replace('?', '%s') if pg else sql,values)
    try:
        with raw: yield Adapter()
    finally: raw.close()


def initialize():
    with connection() as c:
        # Serverless requests may initialize the same fresh PostgreSQL database
        # concurrently. Serialize all social DDL before taking table locks so
        # parallel Command Center requests cannot deadlock one another.
        if c.postgres:
            c.execute('SELECT pg_advisory_xact_lock(?)', (SOCIAL_SCHEMA_LOCK_ID,))
        c.execute('''CREATE TABLE IF NOT EXISTS social_sources(source_id TEXT PRIMARY KEY,payload TEXT NOT NULL,
            signature TEXT NOT NULL,verified_at TEXT NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS social_posts(post_id TEXT PRIMARY KEY,campaign TEXT NOT NULL,
            category TEXT NOT NULL,content TEXT NOT NULL,source_id TEXT NOT NULL REFERENCES social_sources(source_id),
            generated_at TEXT NOT NULL,scheduled_at TEXT NOT NULL,published_at TEXT,x_post_id TEXT,
            status TEXT NOT NULL,failure_reason TEXT,content_hash TEXT NOT NULL UNIQUE,
            signature TEXT NOT NULL,day_key TEXT NOT NULL UNIQUE)''')
        c.execute('CREATE INDEX IF NOT EXISTS social_posts_status_schedule ON social_posts(status,scheduled_at)')
        c.execute('CREATE INDEX IF NOT EXISTS social_sources_verified ON social_sources(verified_at)')
        c.execute('''CREATE TABLE IF NOT EXISTS social_publish_days(day_key TEXT PRIMARY KEY,post_id TEXT NOT NULL)''')
        initialize_schema(c)


def signing_key():
    key=os.getenv('SOCIAL_SOURCE_SIGNING_KEY','')
    if len(key)<32: raise ValueError('SOCIAL_SOURCE_SIGNING_KEY must be at least 32 characters')
    return key.encode()


def sign(value): return hmac.new(signing_key(),encode(value).encode(),hashlib.sha256).hexdigest()


def source_from_databases(week_db,week3_db=PRODUCTION_DATABASE,clock=now):
    """Read verified program evidence; no SQL initialization/grading on Week 3."""
    from backtesting.nfl_week_workflow import metrics
    from backtesting.capture_nfl_game_markets import aware_dt
    regular=metrics(week_db,clock)
    path=Path(week3_db).resolve()
    c=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    c.row_factory=sqlite3.Row
    try:
        rows=c.execute("SELECT game_id,prediction_json,generated_at,kickoff_time FROM nfl_game_predictions WHERE user_id=0 AND season=2026 AND season_type='preseason' AND display_week=3 ORDER BY game_id").fetchall()
        baseline=hashlib.sha256(''.join(r['prediction_json'] for r in rows).encode()).hexdigest()
        if baseline!='a8a405ba262ef59bedb1b7bfcdf1ca4a7c0bf78cafe4b0ff0c1268b7d8b2412a' or len(rows)!=16:
            raise ValueError('Week 3 baseline verification failed')
        if not all(aware_dt(r['generated_at']) and aware_dt(r['kickoff_time']) and aware_dt(r['generated_at'])<aware_dt(r['kickoff_time']) for r in rows):
            raise ValueError('Week 3 pregame lock evidence is incomplete')
        grades=c.execute("SELECT game_id,prediction_result,qualified_wager FROM nfl_experiment_grades WHERE experiment_key='NFL-2026-PRE3'").fetchall()
        if {r['game_id'] for r in rows}!={r['game_id'] for r in grades}: raise ValueError('Week 3 grading incomplete')
        stored_results={r['game_id']:r for r in c.execute("SELECT * FROM nfl_game_results WHERE season=2026 AND season_type='preseason' AND display_week=3")}
        stored_grades={r['game_id']:r for r in grades}
        for prediction in rows:
            result=stored_results.get(prediction['game_id'])
            if not result:raise ValueError('Week 3 final result missing')
            actual=(result['home_team'] if result['home_score']>result['away_score'] else
                    result['away_team'] if result['away_score']>result['home_score'] else None)
            grade='PUSH' if actual is None else 'WIN' if json.loads(prediction['prediction_json'])['winner']==actual else 'LOSS'
            if grade!=stored_grades[prediction['game_id']]['prediction_result']:raise ValueError('Week 3 grade does not match frozen pick/final')
        preseason=dict(phase='preseason',record={s:sum(r['prediction_result']==s for r in grades) for s in ('WIN','LOSS','PUSH')},
                       predictions=len(rows),qualified_wagers=sum(r['qualified_wager'] for r in grades),baseline_hash=baseline)
    finally: c.close()
    # Export only whitelisted, aggregate evidence. No users, emails, subscriber counts, or credentials.
    week_path = Path(week_db).resolve()
    week_connection = sqlite3.connect(week_path.as_uri() + '?mode=ro', uri=True)
    week_connection.row_factory = sqlite3.Row
    try:
        operational_schedule = regular["coverage"]["experiment"]["games"]
        kickoff_by_game = {game["game_id"]: aware_dt(game.get("kickoff_time")) for game in operational_schedule}
        predictions = {row['game_id']: dict(row) for row in week_connection.execute(
            'SELECT game_id,winner,probability,generated_at,model_version,model_hash,prediction_hash FROM forward_predictions')}
        finals = {row['game_id']: dict(row) for row in week_connection.execute('SELECT * FROM forward_finals')}
        grades = {row['game_id']: dict(row) for row in week_connection.execute('SELECT * FROM forward_grades')}
        wagers = {row['game_id'] for row in week_connection.execute('SELECT DISTINCT game_id FROM forward_wagers')}
        markets = {}
        for game_id in predictions:
            captures = week_connection.execute("""SELECT retrieved_at,source_time,event_id,bookmaker,outcomes
                FROM captures WHERE game_id=? AND market_type='h2h' ORDER BY retrieved_epoch""", (game_id,)).fetchall()
            normalized = []
            for market in captures:
                observed_at = aware_dt(market['source_time'] or market['retrieved_at'])
                kickoff = kickoff_by_game.get(game_id)
                # Social comparisons must use legitimate historical pregame
                # observations. A current or post-kickoff price can never be
                # presented as model-vs-market evidence.
                if not observed_at or not kickoff or observed_at >= kickoff:
                    continue
                outcomes = json.loads(market['outcomes'])
                raw = []
                for outcome in outcomes:
                    price = float(outcome['price'])
                    raw.append((-price / (-price + 100)) if price < 0 else (100 / (price + 100)))
                total = sum(raw)
                probabilities = {str(outcome['side']): raw[index] / total for index, outcome in enumerate(outcomes)} if total else {}
                winner = predictions[game_id]['winner']
                if winner in probabilities:
                    normalized.append((market, probabilities))
            if normalized:
                first, latest = normalized[0], normalized[-1]
                winner = predictions[game_id]['winner']
                latest_probability = latest[1][winner]
                markets[game_id] = {
                    'snapshot_id': sha({'event_id': latest[0]['event_id'], 'bookmaker': latest[0]['bookmaker'],
                                        'source_time': latest[0]['source_time']}),
                    'bookmaker': latest[0]['bookmaker'], 'source_time': latest[0]['source_time'],
                    'probability': latest_probability,
                    'edge': predictions[game_id]['probability'] - latest_probability,
                    'movement': latest_probability - first[1][winner] if len(normalized) > 1 else None,
                    'underdog': min(latest[1], key=latest[1].get),
                }
    finally:
        week_connection.close()
    operational_games = []
    for game in regular["coverage"]["experiment"]["games"]:
        prediction = predictions.get(game["game_id"])
        operational_games.append({"game_id": game["game_id"], "away_team": game["away_team"],
                                  "home_team": game["home_team"], "kickoff_time": game["kickoff_time"],
                                  "prediction": prediction, "market": markets.get(game["game_id"]),
                                  "final": finals.get(game["game_id"]),
                                  "grade": ({**grades[game["game_id"]], "qualified_wager": game["game_id"] in wagers}
                                            if game["game_id"] in grades else None)})
    return dict(verified_at=clock().isoformat(),preseason=preseason,
                regular={k:regular[k] for k in ('experiment_id','season','phase','week','manifest_hash','prediction_hash',
                          'predictions','scheduled','graded','winner_record','accuracy','profiles','sample_warning')},
                coverage_games=regular['coverage']['games_with_any_market'],
                operations={"schema_version": 1, "games": operational_games,
                            "checkpoints": {"completed": regular['coverage']['complete_checkpoints'],
                                            "total": regular['coverage']['total_checkpoints']},
                            "provider_status": regular['coverage']['last_status']},
                claims_policy='predictions_are_not_wagers;small_sample_not_future_performance')


def sync_source(week_db,week3_db=PRODUCTION_DATABASE,clock=now):
    source=source_from_databases(week_db,week3_db,clock)
    return store_source(source)


def source_envelope(week_db,week3_db=PRODUCTION_DATABASE,clock=now):
    source=source_from_databases(week_db,week3_db,clock)
    return {'source_id':sha(source),'payload':source,'signature':sign(source)}


def store_source(source):
    source_id=sha(source)
    with connection() as c:
        c.execute('INSERT INTO social_sources VALUES(?,?,?,?) ON CONFLICT(source_id) DO NOTHING',
                  (source_id,encode(source),sign(source),source['verified_at']))
    return {'source_id':source_id,'verified_at':source['verified_at']}


def accept_source_envelope(envelope,clock=now):
    if set(envelope)!={'source_id','payload','signature'} or not isinstance(envelope['payload'],dict):
        raise ValueError('Invalid source envelope')
    source=envelope['payload']
    if envelope['source_id']!=sha(source) or not hmac.compare_digest(str(envelope['signature']),sign(source)):
        raise ValueError('Source envelope authenticity check failed')
    required={'verified_at','preseason','regular','coverage_games','claims_policy'}
    if not required.issubset(source) or set(source)-required-{'operations'} or source['claims_policy']!='predictions_are_not_wagers;small_sample_not_future_performance':
        raise ValueError('Source envelope schema rejected')
    age=clock()-datetime.fromisoformat(source['verified_at'])
    if age<timedelta(0) or age>timedelta(hours=2): raise ValueError('Incoming source is stale')
    initialize()
    return store_source(source)


def load_source(c,source_id=None,clock=now):
    row=c.execute('SELECT * FROM social_sources WHERE source_id=?',(source_id,)).fetchone() if source_id else c.execute('SELECT * FROM social_sources ORDER BY verified_at DESC LIMIT 1').fetchone()
    if not row: raise ValueError('No verified source has been synchronized')
    value=json.loads(row['payload'])
    if sha(value)!=row['source_id'] or not hmac.compare_digest(sign(value),row['signature']):
        raise ValueError('Source authenticity check failed')
    age=clock()-datetime.fromisoformat(value['verified_at'])
    if age<timedelta(0) or age>timedelta(hours=36): raise ValueError('Source is stale; synchronize fresh verified data')
    return row['source_id'],value


POST_TYPES=('GAME_PREVIEW','PLAYER_SPOTLIGHT','ENGAGEMENT','MODEL_RECAP','TREND','PRODUCT_AWARENESS','WATCHLIST')
CATEGORIES=tuple(value.lower() for value in POST_TYPES)
BANNED_SOCIAL_PHRASES=('guaranteed','can\'t lose','free money','100%','easy win','qualified wager',
                       'pregame price','probability calibration','system a','system b','model artifact')
SOCIAL_MARKET_LABELS={
    'passing_tds':'passing TDs','passing tds':'passing TDs',
    'passing_yards':'passing yards','receiving_yards':'receiving yards',
    'rushing_yards':'rushing yards','receptions':'receptions',
}


def cta():
    value=os.getenv('SOCIAL_CTA_URL','')
    if not value: return ''
    parsed=urlsplit(value)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise ValueError('CTA must be a plain HTTPS company URL without credentials/query/fragment')
    expected=os.getenv('SOCIAL_COMPANY_DOMAIN','')
    if not expected or parsed.hostname!=expected: raise ValueError('CTA must match SOCIAL_COMPANY_DOMAIN')
    return ' Explore SmartBets: '+value


def _require(context, *fields):
    missing=[field for field in fields if context.get(field) in (None,'',[],{})]
    if missing: raise ValueError('Grounded social context is missing: '+', '.join(missing))


def validate_social_copy(result,context):
    required={'post_text','post_type','engagement_hook','cta','source_entities','character_count'}
    if set(result)!=required or result['post_type'] not in POST_TYPES: raise ValueError('Invalid social writer output')
    text=str(result['post_text']).strip();lower=text.casefold()
    if not text or len(text)>280 or result['character_count']!=len(text): raise ValueError('Invalid social post length')
    if '@' in text: raise ValueError('Unsolicited mentions are not supported')
    if any(phrase in lower for phrase in BANNED_SOCIAL_PHRASES): raise ValueError('Unsafe or technical social phrasing blocked')
    allowed={str(value) for value in context.get('source_entities',[])}
    if set(map(str,result['source_entities']))-allowed: raise ValueError('Social writer introduced an unsupported entity')
    supplied_numbers=set(re.findall(r'\d+(?:\.\d+)?',encode(context)))
    if set(re.findall(r'\d+(?:\.\d+)?',text))-supplied_numbers: raise ValueError('Social writer introduced an unsupported number')
    return result


def compose_social_post(context,writer=None):
    """Create engaging copy from explicit evidence; an optional AI writer must pass the same guardrails."""
    post_type=str(context.get('post_type') or '').upper();context=dict(context,post_type=post_type)
    if post_type not in POST_TYPES: raise ValueError('Unsupported social post type')
    if writer:
        return validate_social_copy(writer(context),context)
    cta_text=context.get('cta','')
    variant=int(context.get('variant',0))%4
    if post_type=='GAME_PREVIEW':
        _require(context,'away_team','home_team','game_time')
        preview_hooks=('Who are you taking? 👀','What is your read on this one?','Which side has the edge? 👇','Score prediction?')
        hook=preview_hooks[variant]
        text=(f"🚨 {context['away_team']} vs {context['home_team']}\n\n"
              f"Kickoff: {context['game_time']}\n\nThis matchup is on the SmartBets radar.\n\n{hook}{cta_text}")
    elif post_type=='PLAYER_SPOTLIGHT':
        _require(context,'player','market','median')
        market=SOCIAL_MARKET_LABELS.get(str(context['market']).casefold(),context['market'])
        ceiling=f"\n• High range: {context['ceiling']}" if context.get('ceiling') is not None else ''
        text=(f"📈 Player to watch: {context['player']}\n\nProjected {market}:\n"
              f"• Median: {context['median']}{ceiling}\n\nOver or under? 👀{cta_text}")
        hook='Over or under?'
    elif post_type=='ENGAGEMENT':
        _require(context,'matchups')
        options='\n'.join(f"{chr(65+i)}) {matchup}" for i,matchup in enumerate(context['matchups'][:4]))
        poll_hooks=('Which matchup are you watching first?','Where is your strongest lean?','Which game steals the show?','Pick one game to watch—what is it?')
        hook=poll_hooks[variant]
        text=f"🏈 The slate is taking shape.\n\n{hook}\n\n{options}\n\nDrop your pick below 👇{cta_text}"
    elif post_type=='MODEL_RECAP':
        _require(context,'record','sample_size')
        recap_hooks=('Which result should we break down next? 👀','What stood out to you?','Which matchup deserves a deeper look?','What should the model review next?')
        hook=recap_hooks[variant]
        text=(f"📊 MODEL RECAP\n\nRecord: {context['record']} across {context['sample_size']} predictions.\n\n{hook}{cta_text}")
    elif post_type=='TREND':
        _require(context,'trend')
        text=f"👀 Matchup trend to watch:\n\n{context['trend']}\n\nDoes it change your read on the game?{cta_text}"
        hook='Does it change your read?'
    elif post_type=='WATCHLIST':
        _require(context,'players')
        names='\n'.join(f"{icon} {player}" for icon,player in zip(('🔥','📈','🎯'),context['players'][:3]))
        text=f"👀 SmartBets Watchlist\n\nPlayers on our model radar:\n\n{names}\n\nWho should we break down first?{cta_text}"
        hook='Who should we break down first?'
    else:
        _require(context,'games_count','predictions_count')
        product_hooks=('See the full slate.','Follow every matchup in one place.','See what the model is tracking.','Open the Command Center.')
        hook=product_hooks[variant]
        text=(f"Every game. Every prediction. One clear view.\n\nSmartBetSports is tracking "
              f"{context['games_count']} active games and {context['predictions_count']} predictions.\n\n{hook}{cta_text}")
    result={'post_text':text,'post_type':post_type,'engagement_hook':hook,'cta':cta_text,
            'source_entities':list(context.get('source_entities',[])),'character_count':len(text)}
    return validate_social_copy(result,context)


def social_context(source,post_type,day=0):
    games=(source.get('operations') or {}).get('games') or []
    p,r=source['preseason'],source['regular'];post_type=post_type.upper()
    base={'sport':'NFL','post_type':post_type,'cta':'','source_entities':[],'variant':int(day)//4}
    if post_type=='GAME_PREVIEW':
        if not games: raise ValueError('No verified game is available for a preview')
        game=games[int(day)%len(games)]
        kickoff=datetime.fromisoformat(game['kickoff_time'].replace('Z','+00:00'))
        when=kickoff.astimezone(ZoneInfo('America/New_York')).strftime('%b %d · %I:%M %p ET')
        return dict(base,away_team=game['away_team'],home_team=game['home_team'],game_time=when,
                    source_entities=[game['away_team'],game['home_team'],game['game_id']])
    if post_type=='ENGAGEMENT':
        if not games: raise ValueError('No verified matchups are available')
        start=(int(day)*4)%len(games);selected=(games+games)[start:start+4]
        matchups=[f"{game['away_team']} vs {game['home_team']}" for game in selected]
        return dict(base,matchups=matchups,source_entities=[team for game in selected for team in (game['away_team'],game['home_team'])])
    if post_type=='MODEL_RECAP':
        completed=r if r.get('graded') else p
        if not completed.get('predictions') or not completed.get('record',completed.get('winner_record')): raise ValueError('No completed prediction sample is available')
        record=completed.get('record') or completed['winner_record'];value=f"{record['WIN']}-{record['LOSS']}-{record['PUSH']}"
        sample_size=completed.get('graded',completed['predictions'])
        return dict(base,record=value,sample_size=sample_size,source_entities=[value])
    return dict(base,post_type='PRODUCT_AWARENESS',games_count=r['scheduled'],predictions_count=r['predictions'],
                source_entities=[r['scheduled'],r['predictions']])


def render(day,source):
    day=int(day)
    rotation=('GAME_PREVIEW','ENGAGEMENT','PRODUCT_AWARENESS','MODEL_RECAP')
    post_type=rotation[day%len(rotation)]
    if post_type in {'GAME_PREVIEW','ENGAGEMENT'} and not (source.get('operations') or {}).get('games'):
        post_type='PRODUCT_AWARENESS'
    context=social_context(source,post_type,day)
    configured_cta=cta()
    if day%7 in {2,6}: context['cta']=configured_cta
    result=compose_social_post(context)
    return result['post_type'].lower(),result['post_text']


def similarity(text):
    return re.sub(r'\s+',' ',re.sub(r'https://\S+|\d+(?:\.\d+)?','',text.lower())).strip()


EVENT_TEMPLATES={'slate_frozen':2,'pregame_picks_ready':0,'sunday_recap':3,'final_weekly_results':3}


def generate(clock=now,event=None):
    start=datetime.fromisoformat(os.environ['SOCIAL_CAMPAIGN_START']).date()
    day=(clock().date()-start).days
    if day<0: raise ValueError('Campaign has not started')
    campaign=os.getenv('SOCIAL_CAMPAIGN','smartbets-launch-v1')
    today=clock().date().isoformat()
    post_id=sha(dict(campaign=campaign,day=today))[:32]
    with connection() as c:
        if c.postgres: c.execute('SELECT pg_advisory_xact_lock(734121)')
        else: c.execute('BEGIN IMMEDIATE')
        existing=c.execute('SELECT * FROM social_posts WHERE post_id=?',(post_id,)).fetchone()
        if existing: return dict(existing)
        source_id,source=load_source(c,clock=clock)
        if event:
            if event not in EVENT_TEMPLATES:raise ValueError('Unsupported editorial event')
            regular=source['regular']
            if event=='slate_frozen' and regular['predictions']!=regular['scheduled']:raise ValueError('Slate is not fully frozen')
            if event=='pregame_picks_ready' and source['coverage_games']==0:raise ValueError('No verified pregame markets')
            if event=='sunday_recap' and regular['graded']==0:raise ValueError('No graded regular-season results')
            if event=='final_weekly_results' and regular['graded']!=regular['scheduled']:raise ValueError('Weekly grading incomplete')
            day=EVENT_TEMPLATES[event]
        category,content=render(day,source)
        # Guard the current editorial week against repetitive copy while allowing
        # a grounded format to return later with fresh game context.
        for prior in c.execute('SELECT content FROM social_posts ORDER BY scheduled_at DESC LIMIT 7').fetchall():
            if SequenceMatcher(None,similarity(content),similarity(prior['content'])).ratio()>=.82:
                raise ValueError('Near-duplicate content blocked; editorial review required')
        generated=clock().isoformat()
        signed=dict(post_id=post_id,content=content,source_id=source_id,day=day,day_key=today,campaign=campaign)
        # Signature also covers template index, stored as category|index for deterministic revalidation.
        c.execute('INSERT INTO social_posts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (post_id,campaign,f'{category}|{day}',content,source_id,generated,generated,None,None,
                   'DRAFT',None,sha(content),sign(signed),today))
        return dict(c.execute('SELECT * FROM social_posts WHERE post_id=?',(post_id,)).fetchone())


def publish_one(post_id,clock=now,client_factory=OfficialX):
    initialize()
    with connection() as c:
        settings=social_settings.load_settings(c)
    if settings['paused']:
        return {'status':'PAUSED','post_id':post_id,'published':False}
    if settings['dry_run'] or not settings['auto_publish']:
        return {'status':'DRY_RUN','post_id':post_id,'published':False}
    if os.getenv('VERCEL_ENV') not in (None,'production'):
        raise ValueError('Publishing is prohibited from preview/development deployments')
    media=[];reply_to=None;claim_key='post:'+post_id
    with connection() as c:
        if c.postgres: c.execute('SELECT pg_advisory_xact_lock(734121)')
        else: c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM social_posts WHERE post_id=?',(post_id,)).fetchone()
        if not row: raise ValueError('Post not found')
        # FAILED is considered only after the durable claim has proved that the
        # failure happened before any public tweet write.  All other terminal
        # states remain non-retryable.
        if row['status'] not in ('DRAFT','READY','FAILED'): return {'status':row['status'],'published':False}
        _,source=load_source(c,row['source_id'],clock)
        details=c.execute('SELECT * FROM social_post_details WHERE post_id=?',(post_id,)).fetchone()
        if details:
            context=decoded(details['source_context_json'],{})
            content=validate_caption(row['content'],context)
            signed=dict(post_id=post_id,content=content,source_id=row['source_id'],
                        opportunity_id=details['opportunity_id'],source_hash=details['source_hash'])
            integrity=sha(dict(signed=signed,context=context))
            if (sha(content)!=row['content_hash'] or integrity!=details['integrity_hash']
                    or not hmac.compare_digest(sign(signed),row['signature'])):
                raise ValueError('Post content/stat authenticity check failed')
            media_ids=decoded(details['media_asset_ids_json'],[])
            for media_id in media_ids[-4:]:
                asset=c.execute('SELECT * FROM social_media_assets WHERE media_asset_id=?',(media_id,)).fetchone()
                if not asset or asset['status']!='READY' or asset['source_hash']!=details['source_hash']:
                    raise ValueError('Post media authenticity check failed')
                media.append(dict(asset))
            reply_to=details['reply_to_x_post_id']
        else:
            category,index=row['category'].rsplit('|',1)
            _,content=render(int(index),source)
            signed=dict(post_id=post_id,content=content,source_id=row['source_id'],day=int(index),day_key=row['day_key'],campaign=row['campaign'])
            if (content!=row['content'] or sha(content)!=row['content_hash'] or not hmac.compare_digest(sign(signed),row['signature'])):
                raise ValueError('Post content/stat authenticity check failed')
        if row['day_key']!=clock().date().isoformat() or datetime.fromisoformat(row['scheduled_at'])>clock():
            raise ValueError('Post is not scheduled for today')
        prior=c.execute('SELECT state,last_error_code,attempt_count FROM social_delivery_claims WHERE idempotency_key=?',(claim_key,)).fetchone()
        retryable_failure=prior and prior['state']=='FAILED' and prior['last_error_code'] in (
            'ACCOUNT_OR_AUTH_VERIFICATION_FAILED','MEDIA_OR_DELIVERY_PRECHECK_FAILED')
        if row['status']=='FAILED' and not retryable_failure:
            return {'status':row['status'],'published':False}
        if prior and (not retryable_failure or int(prior['attempt_count'])>=3):
            return {'status':prior['state'],'published':False}
        claimed=clock().isoformat()
        if prior:
            c.execute("UPDATE social_delivery_claims SET state='PUBLISHING',attempt_count=attempt_count+1,last_error_code=NULL,updated_at=? WHERE idempotency_key=?",
                      (claimed,claim_key))
        else:
            c.execute("INSERT INTO social_delivery_claims(idempotency_key,post_id,claimed_at,state,attempt_count,updated_at) VALUES(?,? ,?,'PUBLISHING',1,?)",
                      (claim_key,post_id,claimed,claimed))
        c.execute("UPDATE social_posts SET status='PUBLISHING' WHERE post_id=?",(post_id,))
    # A durable claim precedes all external writes. Crashes never cause blind reposting.
    status,reason,x_id,remote_media,tweet_attempted='FAILED',None,None,[],False
    try:
        client=client_factory();client.verify_company()
    except Exception:
        reason='ACCOUNT_OR_AUTH_VERIFICATION_FAILED'
    else:
        try:
            store=storage_factory() if media else None
            for asset in media:
                payload=store.get(asset['storage_key'])
                content_type='video/mp4' if asset['media_type']=='video' else 'image/png'
                remote_media.append(client.upload(payload,content_type,asset['alt_text']))
            tweet_attempted=True
            response=client.post(content,remote_media,reply_to) if (remote_media or reply_to) else client.post(content)
            if response.status_code in (200,201):
                x_id=response.json().get('data',{}).get('id')
                status='PUBLISHED' if x_id else 'UNKNOWN'
                reason=None if x_id else 'MISSING_REMOTE_ID'
            elif response.status_code>=500:
                status,reason='UNKNOWN','REMOTE_SERVER_ERROR_RECONCILE'
            else:
                status,reason='FAILED',f'X_HTTP_{response.status_code}'
        except Exception:
            status,reason=('UNKNOWN','UNCERTAIN_DELIVERY_RECONCILE') if tweet_attempted else ('FAILED','MEDIA_OR_DELIVERY_PRECHECK_FAILED')
    with connection() as c:
        c.execute('UPDATE social_posts SET status=?,failure_reason=?,x_post_id=?,published_at=? WHERE post_id=?',
                  (status,reason,x_id,clock().isoformat() if status=='PUBLISHED' else None,post_id))
        c.execute('UPDATE social_delivery_claims SET state=?,remote_media_ids_json=?,remote_post_id=?,last_error_code=?,updated_at=? WHERE idempotency_key=?',
                  (status,encode(remote_media),x_id,reason,clock().isoformat(),claim_key))
        if details:
            c.execute('UPDATE social_opportunities SET status=?,updated_at=? WHERE opportunity_id=?',
                      (status,clock().isoformat(),details['opportunity_id']))
            for local_id,remote_id in zip(decoded(details['media_asset_ids_json'],[])[-4:],remote_media):
                c.execute('UPDATE social_media_assets SET remote_media_id=?,updated_at=? WHERE media_asset_id=?',
                          (remote_id,clock().isoformat(),local_id))
            if reason=='MEDIA_OR_DELIVERY_PRECHECK_FAILED':
                for local_id in decoded(details['media_asset_ids_json'],[])[-4:]:
                    c.execute('UPDATE social_media_assets SET retry_count=retry_count+1,error_code=?,updated_at=? WHERE media_asset_id=?',
                              (reason,clock().isoformat(),local_id))
    logger.info("social_publish_result", extra={"postId": post_id, "status": status,
                                                "mediaCount": len(remote_media), "failureCode": reason})
    return dict(status=status,post_id=post_id,published=status=='PUBLISHED',x_post_id=x_id,failure_reason=reason)


def daily(clock=now):
    initialize()
    discovered=discover_opportunities(clock)
    processed=process_queue(clock=clock,limit=2)
    if processed['processed']:
        return dict(discovery=discovered,queue=processed,post=processed['items'][0].get('post'),result=processed['items'][0].get('result'))
    # The compatibility generate() entry point remains available for explicit
    # legacy callers, but the automated worker never manufactures a generic
    # daily post when no verified opportunity is due.
    return dict(discovery=discovered,queue=processed,post=None,
                result={'status':'NO_DUE_OPPORTUNITY','published':False})


def discover_opportunities(clock=now):
    initialize()
    with connection() as c:
        source_id,source=load_source(c,clock=clock)
        settings=social_settings.load_settings(c)
        if not settings['automation_enabled'] or settings['paused']:
            return {'status':'PAUSED','discovered':0,'opportunities':0,'items':[]}
        result=opportunity_service.discover(c,source_id,source,settings,sha,clock)
        logger.info("social_opportunity_discovery", extra={"sourceId":source_id,"discovered":result['discovered'],
                                                           "queued":result['queued'],"review":result['review'],"skipped":result['skipped']})
        return result


def process_queue(clock=now,limit=2,client_factory=OfficialX):
    initialize();items=[]
    with connection() as c:
        settings=social_settings.load_settings(c)
        queued=queue_scheduler.due(c,settings,clock,limit)
        for opportunity in queued:
            post=queue_scheduler.materialize(c,opportunity['opportunity_id'],sha=sha,sign=sign,cta=cta(),clock=clock)
            context=post.pop('context',None)
            graphics_enabled=os.getenv('SOCIAL_DETERMINISTIC_GRAPHICS_ENABLED','true').lower()=='true'
            if context and (settings['ai_images_enabled'] or graphics_enabled):
                try:
                    asset=generate_media(c,opportunity,post,context,settings,sha,clock=clock)
                    if asset:
                        c.execute('UPDATE social_post_details SET media_asset_ids_json=?,media_type=?,alt_text=?,updated_at=? WHERE post_id=?',
                                  (encode([asset['media_asset_id']]),asset['media_type'],asset['alt_text'],clock().isoformat(),post['post_id']))
                except Exception:
                    c.execute("UPDATE social_opportunities SET status='REVIEW',skip_reason='MEDIA_GENERATION_FAILED',updated_at=? WHERE opportunity_id=?",
                              (clock().isoformat(),opportunity['opportunity_id']))
                    items.append({'opportunity_id':opportunity['opportunity_id'],'post':post,'status':'REVIEW','result':None})
                    continue
            items.append({'opportunity_id':opportunity['opportunity_id'],'post':post})
    for item in items:
        if item.get('status')=='REVIEW':continue
        item['result']=publish_one(item['post']['post_id'],clock,client_factory)
        item['status']=item['result']['status']
    logger.info("social_queue_processed", extra={"processed":len(items),"statuses":[item.get('status') for item in items]})
    return {'processed':len(items),'items':items}


def reconcile_published(post_id,x_post_id,clock=now,client_factory=OfficialX):
    """Read-only remote verification of an uncertain delivery; never reposts."""
    client=client_factory();client.verify_company()
    remote=client.lookup(x_post_id)
    if remote.status_code!=200:raise ValueError('Cannot verify remote post')
    data=remote.json().get('data',{})
    with connection() as c:
        row=c.execute('SELECT * FROM social_posts WHERE post_id=?',(post_id,)).fetchone()
        if not row or row['status'] not in ('PUBLISHING','UNKNOWN'):raise ValueError('Only uncertain posts can be reconciled')
        if (data.get('id')!=x_post_id or data.get('author_id')!=os.getenv('X_EXPECTED_USER_ID')
                or data.get('text')!=row['content']):raise ValueError('Remote author/content mismatch')
        c.execute("UPDATE social_posts SET status='PUBLISHED',x_post_id=?,failure_reason='RECONCILED_REMOTE_POST',published_at=? WHERE post_id=?",
                  (x_post_id,clock().isoformat(),post_id))
    return dict(status='PUBLISHED',post_id=post_id,x_post_id=x_post_id,reconciled=True)


def campaign_report():
    initialize()
    with connection() as c:
        posts=[dict(r) for r in c.execute('SELECT post_id,campaign,category,content,source_id,generated_at,scheduled_at,published_at,x_post_id,status,failure_reason,content_hash FROM social_posts ORDER BY scheduled_at')]
        source=c.execute('SELECT verified_at FROM social_sources ORDER BY verified_at DESC LIMIT 1').fetchone()
        alerts=[]
        if not source: alerts.append('NO_VERIFIED_SOURCE')
        else:
            age=now()-datetime.fromisoformat(source['verified_at'])
            if age<timedelta(0) or age>timedelta(hours=36):alerts.append('SOURCE_STALE')
        if posts and posts[-1]['status'] in ('FAILED','UNKNOWN','PUBLISHING'):
            alerts.append('LAST_DELIVERY_REQUIRES_REVIEW')
        settings=social_settings.load_settings(c)
        return {'posts':posts,'categories':list(CATEGORIES),'alerts':alerts,'settings':settings,
                'latest_source_at':source['verified_at'] if source else None,
                'dry_run':settings['dry_run'],'auto_publish':settings['auto_publish'],'paused':settings['paused']}
