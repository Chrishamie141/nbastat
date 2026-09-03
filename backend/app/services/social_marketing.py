"""Company-only official X API publishing with verified, deterministic claims."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit

import requests
from database_safety import assert_postgres_allowed, assert_sqlite_target, PRODUCTION_DATABASE


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
        if c.postgres:
            # Dedicated server role only. No browser-client RLS policies are installed.
            for table in ('social_sources','social_posts','social_publish_days'):
                c.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
                c.execute(f'REVOKE ALL ON TABLE {table} FROM anon, authenticated')


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
    operational_games = [{"game_id": game["game_id"], "away_team": game["away_team"],
                          "home_team": game["home_team"], "kickoff_time": game["kickoff_time"]}
                         for game in regular["coverage"]["experiment"]["games"]]
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


CATEGORIES=('transparent_performance','preparation','how_it_works','product_features','data_education',
            'market_coverage','product_cta','model_insight','responsible_bankroll','results_recap',
            'market_movement','pregame_process','subscriber_cta','weekly_transparency',
            'no_retroactive_bets','dashboard_literacy','calibration_education','price_shopping',
            'sample_size','timestamp_integrity','coverage_update','buyer_diligence','prediction_review',
            'push_handling','model_disagreement','research_checklist','weekly_market_review','build_log')


def cta():
    value=os.getenv('SOCIAL_CTA_URL','')
    if not value: return ''
    parsed=urlsplit(value)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise ValueError('CTA must be a plain HTTPS company URL without credentials/query/fragment')
    expected=os.getenv('SOCIAL_COMPANY_DOMAIN','')
    if not expected or parsed.hostname!=expected: raise ValueError('CTA must match SOCIAL_COMPANY_DOMAIN')
    return ' Explore SmartBets: '+value


def render(day,source):
    p,r=source['preseason'],source['regular'];record=p['record'];regular=r['winner_record']
    messages=[
        f"Our frozen preseason test: {record['WIN']}-{record['LOSS']}-{record['PUSH']} across {p['predictions']} PREDICTIONS; {p['qualified_wagers']} qualified wagers. A small preseason sample, not a promise of future returns.",
        f"Regular-season Week {r['week']} preparation: {r['predictions']}/{r['scheduled']} predictions frozen. Pregame prices and qualified wagers are tracked separately from picks.",
        'A prediction is a model estimate. A qualified wager also needs a valid pregame price and must clear our probability and edge rules. Missing prices mean no wager.',
        'SmartBets keeps prediction timestamps, input provenance and market snapshots separate. That makes it possible to review what was known before kickoff.',
        'A winning pick does not prove a profitable betting strategy. Price, losses, pushes and sample size all matter. We report predictions separately from qualified wagers.',
        f"Verified pregame market coverage currently reaches {source['coverage_games']}/{r['scheduled']} games in our regular-season experiment. Gaps stay visible; missing prices are never filled with postgame odds.",
        'Explore how SmartBets turns football research into traceable predictions. Review the process and product before deciding whether it fits your needs. No outcome is guaranteed.',
        'Model insight starts with an honest question: which inputs were available at prediction time? SmartBets freezes the evidence so later results cannot rewrite the original reasoning.',
        'Responsible research means setting limits, accepting uncertainty and never chasing losses. A model probability is not a promise. You can follow the analysis without placing a wager.',
        f"Regular-season prediction recap so far: {regular['WIN']}-{regular['LOSS']}-{regular['PUSH']}, with {r['graded']} graded predictions. This is a developing sample, not an established future win rate. Wagers are a separate record.",
        'Market movement needs timestamped observations. SmartBets compares pregame snapshots without pretending current prices were available earlier. No verified capture means no movement claim.',
        'Our pregame process: freeze the prediction, capture a valid price, check qualification, then preserve the evidence. Final scores arrive later and cannot create a retroactive wager.',
        'Considering SmartBets? Start with the research process, transparent limitations and product details. A subscription provides product access, not guaranteed betting results.',
        f"Transparency check: {r['predictions']} regular-season predictions frozen for {r['scheduled']} scheduled games. We keep preseason and regular-season records separate, including pushes and unpriced picks.",
        'Results cannot create a bet after the fact. SmartBets only recognizes a wager when a valid price was captured before kickoff and the published qualification rules were met.',
        'Read a betting dashboard in layers: prediction record, priced opportunities, qualified wagers and realized returns answer different questions. SmartBets keeps those layers separate.',
        'A useful probability should be calibrated, not merely confident. SmartBets measures whether events labeled 60% actually occur near that rate over a meaningful out-of-sample history.',
        'The same prediction can have different value at different sportsbooks. SmartBets records bookmaker, line, price and retrieval time so price shopping is measurable rather than assumed.',
        'Small samples can look spectacular or terrible by chance. We preserve every eligible result and report the sample size so one streak cannot quietly become a long-term claim.',
        'Pregame evidence needs a clock. SmartBets stores when data was retrieved, the provider timestamp and kickoff time, then rejects captures made at or after kickoff.',
        f"Coverage update: verified pregame markets are available for {source['coverage_games']} of {r['scheduled']} games. Uncovered games remain predictions, not retroactively priced wagers.",
        'Evaluating SmartBets? Check the timestamped inputs, frozen predictions, complete grading and separation of picks from wagers. Transparent limitations matter as much as headline results.',
        'A postgame review should ask two questions: was the forecast direction right, and was the pregame price good enough to bet? SmartBets records those answers independently.',
        'Pushes are outcomes, not missing data. SmartBets keeps them in the record and applies explicit settlement rules instead of quietly dropping inconvenient results.',
        'Model disagreement is information. Comparing a forecast with the market can reveal an opportunity, but it can also reveal missing context. SmartBets preserves both estimates for review.',
        'Research checklist: use only information available before kickoff, freeze the prediction, capture a real price, apply the rules consistently and grade every eligible result.',
        f"Weekly market review: {r['graded']} regular-season predictions are graded and verified coverage spans {source['coverage_games']} games. The wager record remains separate from pick accuracy.",
        'Build log: SmartBets is designed so provider failures, stale prices and missing coverage remain visible. A trustworthy system should fail closed instead of inventing certainty.',
    ]
    day=int(day)
    index=day%len(messages)
    # Links are deliberately limited to two days per seven-day block. Most posts
    # should teach or report something useful instead of repeatedly selling.
    link_days={3,6,10,12,17,20,24,27}
    configured_cta=cta()
    content=messages[index]+(configured_cta if index in link_days else '')
    if day>=len(messages):
        start=datetime.fromisoformat(os.environ['SOCIAL_CAMPAIGN_START']).date()
        content=f"{(start+timedelta(days=day)).strftime('%b %d')} research update: "+content
    if len(content)>280: raise ValueError('Post exceeds conservative 280-character limit; shorten configured CTA')
    if '@' in content: raise ValueError('Unsolicited mentions are not supported')
    return CATEGORIES[index],content


def similarity(text):
    return re.sub(r'\s+',' ',re.sub(r'https://\S+|\d+(?:\.\d+)?','',text.lower())).strip()


EVENT_TEMPLATES={'slate_frozen':1,'pregame_picks_ready':5,'sunday_recap':9,'final_weekly_results':13}


def generate(clock=now,event=None):
    start=datetime.fromisoformat(os.environ['SOCIAL_CAMPAIGN_START']).date()
    day=(clock().date()-start).days
    if day<0: raise ValueError('Campaign has not started')
    campaign=os.getenv('SOCIAL_CAMPAIGN','smartbets-launch-v1')
    today=clock().date().isoformat()
    with connection() as c:
        if c.postgres: c.execute('SELECT pg_advisory_xact_lock(734121)')
        else: c.execute('BEGIN IMMEDIATE')
        existing=c.execute('SELECT * FROM social_posts WHERE day_key=?',(today,)).fetchone()
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
        # A complete four-week topic rotation prevents rapid template reuse while allowing
        # a dated, newly verified research update after a full cycle.
        for prior in c.execute('SELECT content FROM social_posts ORDER BY scheduled_at DESC LIMIT 27').fetchall():
            if SequenceMatcher(None,similarity(content),similarity(prior['content'])).ratio()>=.82:
                raise ValueError('Near-duplicate content blocked; editorial review required')
        post_id=sha(dict(campaign=campaign,day=today))[:32]
        generated=clock().isoformat()
        signed=dict(post_id=post_id,content=content,source_id=source_id,day=day,day_key=today,campaign=campaign)
        # Signature also covers template index, stored as category|index for deterministic revalidation.
        c.execute('INSERT INTO social_posts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  (post_id,campaign,f'{category}|{day}',content,source_id,generated,generated,None,None,
                   'DRAFT',None,sha(content),sign(signed),today))
        return dict(c.execute('SELECT * FROM social_posts WHERE post_id=?',(post_id,)).fetchone())


class OfficialX:
    def __init__(self):
        from requests_oauthlib import OAuth1
        names=('X_API_KEY','X_API_SECRET','X_ACCESS_TOKEN','X_ACCESS_TOKEN_SECRET')
        credentials=[os.getenv(name) for name in names]
        if not all(credentials): raise ValueError('Four OAuth 1.0a user-context credentials are required')
        self.session=requests.Session()
        self.session.auth=OAuth1(*credentials)

    def verify_company(self):
        expected=os.getenv('X_EXPECTED_USER_ID')
        if not expected: raise ValueError('X_EXPECTED_USER_ID is required')
        response=self.session.get('https://api.x.com/2/users/me',timeout=15,allow_redirects=False)
        if response.status_code!=200 or response.json().get('data',{}).get('id')!=expected:
            raise ValueError('X company-account verification failed')

    def post(self,content):
        return self.session.post('https://api.x.com/2/tweets',json={'text':content},timeout=20,allow_redirects=False)

    def lookup(self,post_id):
        if not re.fullmatch(r'[0-9]+',post_id):raise ValueError('Invalid X post ID')
        return self.session.get(f'https://api.x.com/2/tweets/{post_id}',params={'tweet.fields':'author_id'},timeout=15,allow_redirects=False)


def publish_one(post_id,clock=now,client_factory=OfficialX):
    if os.getenv('DRY_RUN','true').lower()!='false' or not enabled('SOCIAL_AUTO_PUBLISH'):
        return {'status':'DRY_RUN','post_id':post_id,'published':False}
    if os.getenv('VERCEL_ENV') not in (None,'production'):
        raise ValueError('Publishing is prohibited from preview/development deployments')
    with connection() as c:
        if c.postgres: c.execute('SELECT pg_advisory_xact_lock(734121)')
        else: c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM social_posts WHERE post_id=?',(post_id,)).fetchone()
        if not row: raise ValueError('Post not found')
        if row['status']!='DRAFT': return {'status':row['status'],'published':False}
        _,source=load_source(c,row['source_id'],clock)
        category,index=row['category'].rsplit('|',1)
        _,content=render(int(index),source)
        signed=dict(post_id=post_id,content=content,source_id=row['source_id'],day=int(index),day_key=row['day_key'],campaign=row['campaign'])
        if (content!=row['content'] or sha(content)!=row['content_hash'] or not hmac.compare_digest(sign(signed),row['signature'])):
            raise ValueError('Post content/stat authenticity check failed')
        if row['day_key']!=clock().date().isoformat() or datetime.fromisoformat(row['scheduled_at'])>clock():
            raise ValueError('Post is not scheduled for today')
        if c.execute('SELECT 1 FROM social_publish_days WHERE day_key=?',(row['day_key'],)).fetchone():
            return {'status':'DAY_ALREADY_CLAIMED','published':False}
        c.execute('INSERT INTO social_publish_days VALUES(?,?)',(row['day_key'],post_id))
        c.execute("UPDATE social_posts SET status='PUBLISHING' WHERE post_id=?",(post_id,))
    # A durable claim precedes all external writes. Crashes never cause blind reposting.
    status,reason,x_id='FAILED',None,None
    try:
        client=client_factory();client.verify_company()
    except Exception:
        reason='ACCOUNT_OR_AUTH_VERIFICATION_FAILED'
    else:
        try:
            response=client.post(content)
            if response.status_code in (200,201):
                x_id=response.json().get('data',{}).get('id')
                status='PUBLISHED' if x_id else 'UNKNOWN'
                reason=None if x_id else 'MISSING_REMOTE_ID'
            elif response.status_code>=500:
                status,reason='UNKNOWN','REMOTE_SERVER_ERROR_RECONCILE'
            else:
                status,reason='FAILED',f'X_HTTP_{response.status_code}'
        except Exception:
            status,reason='UNKNOWN','UNCERTAIN_DELIVERY_RECONCILE'
    with connection() as c:
        c.execute('UPDATE social_posts SET status=?,failure_reason=?,x_post_id=?,published_at=? WHERE post_id=?',
                  (status,reason,x_id,clock().isoformat() if status=='PUBLISHED' else None,post_id))
    return dict(status=status,post_id=post_id,published=status=='PUBLISHED',x_post_id=x_id,failure_reason=reason)


def daily(clock=now):
    initialize()
    post=generate(clock)
    result=publish_one(post['post_id'],clock)
    return dict(post=post,result=result)


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
        return {'posts':posts,'categories':list(CATEGORIES),'alerts':alerts,
                'latest_source_at':source['verified_at'] if source else None,
                'dry_run':os.getenv('DRY_RUN','true').lower()!='false','auto_publish':enabled('SOCIAL_AUTO_PUBLISH')}
