"""Read-only aggregate command center for internal SmartBets operators."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os

from backend.app.database import get_db_connection
from backend.app.services import social_marketing


def _now(): return datetime.now(timezone.utc)


def _next_daily(at, hour=13):
    candidate=at.replace(hour=hour,minute=0,second=0,microsecond=0)
    if candidate<=at:candidate+=timedelta(days=1)
    return candidate.isoformat()


def _buyer_metrics():
    with get_db_connection() as c:
        row=c.execute("""SELECT
            COUNT(*) AS registered,
            SUM(CASE WHEN is_active=1 THEN 1 ELSE 0 END) AS active_accounts,
            SUM(CASE WHEN subscription_status IN ('active','trialing') THEN 1 ELSE 0 END) AS members,
            SUM(CASE WHEN access_source='stripe_paid' AND subscription_status IN ('active','trialing') THEN 1 ELSE 0 END) AS paid_members,
            SUM(CASE WHEN access_source='stripe_promotion' AND subscription_status IN ('active','trialing') THEN 1 ELSE 0 END) AS promotional_members,
            SUM(CASE WHEN subscription_status='past_due' THEN 1 ELSE 0 END) AS past_due,
            SUM(CASE WHEN subscription_cancel_at_period_end=1 AND subscription_status IN ('active','trialing') THEN 1 ELSE 0 END) AS canceling
            FROM users""").fetchone()
    return {key:int(row[key] or 0) for key in row.keys()}


def command_center(clock=_now):
    at=clock()
    with social_marketing.connection() as c:
        source_row=c.execute('SELECT payload,verified_at FROM social_sources ORDER BY verified_at DESC LIMIT 1').fetchone()
        if not source_row:raise ValueError('No verified social source is available')
        source=json.loads(source_row['payload'])
        posts=[dict(row) for row in c.execute("""SELECT post_id,category,content,scheduled_at,published_at,x_post_id,status,failure_reason
            FROM social_posts ORDER BY scheduled_at DESC LIMIT 20""").fetchall()]
        status_rows=c.execute('SELECT status,COUNT(*) AS count FROM social_posts GROUP BY status').fetchall()
    source_at=datetime.fromisoformat(source_row['verified_at'])
    source_age=max(0,(at-source_at).total_seconds())
    post_counts={row['status']:int(row['count']) for row in status_rows}
    regular=source['regular'];preseason=source['preseason'];record=regular['winner_record']
    alerts=[]
    source_stale=source_age>36*3600
    delivery_attention=bool(posts and posts[0]['status'] in ('FAILED','UNKNOWN','PUBLISHING'))
    if source_stale:alerts.append({'severity':'critical','code':'SOCIAL_SOURCE_STALE','message':'Verified source is older than 36 hours; publishing is blocked.'})
    if delivery_attention:
        alerts.append({'severity':'critical' if posts[0]['status']=='UNKNOWN' else 'warning','code':'SOCIAL_DELIVERY_REVIEW','message':f"Latest social delivery is {posts[0]['status']}."})
    if not social_marketing.enabled('SOCIAL_SCHEDULER_ENABLED'):
        alerts.append({'severity':'warning','code':'SOCIAL_SCHEDULER_DISABLED','message':'Daily social scheduler is disabled.'})
    if source['coverage_games']<regular['scheduled']:
        alerts.append({'severity':'warning','code':'MARKET_COVERAGE_INCOMPLETE','message':f"Verified markets cover {source['coverage_games']} of {regular['scheduled']} games."})
    buyers=_buyer_metrics()
    if buyers['past_due']:alerts.append({'severity':'warning','code':'PAST_DUE_MEMBERS','message':f"{buyers['past_due']} membership account(s) are past due."})
    feed=[]
    for post in posts:
        feed.append({'type':'social','at':post['published_at'] or post['scheduled_at'],'title':f"X post · {post['status']}",'detail':post['content'],'status':post['status'],'url':f"https://x.com/SmartBetSports/status/{post['x_post_id']}" if post['x_post_id'] else None})
    feed.extend([
        {'type':'data','at':source_row['verified_at'],'title':'Verified source synchronized','detail':f"Week {regular['week']} · {regular['predictions']}/{regular['scheduled']} predictions · {source['coverage_games']} games priced",'status':'HEALTHY'},
        {'type':'experiment','at':source_row['verified_at'],'title':'Regular-season experiment','detail':f"{regular['graded']} graded · {record['WIN']}-{record['LOSS']}-{record['PUSH']} record",'status':'ACTIVE'},
        {'type':'experiment','at':source_row['verified_at'],'title':'Frozen Week 3 baseline','detail':f"{preseason['record']['WIN']}-{preseason['record']['LOSS']}-{preseason['record']['PUSH']} across {preseason['predictions']} predictions",'status':'VERIFIED'},
    ])
    feed.sort(key=lambda item:item['at'] or '',reverse=True)
    return {
        'generatedAt':at.isoformat(),'overallStatus':'ATTENTION' if alerts else 'HEALTHY','alerts':alerts,
        'systems':{
            'api':{'status':'HEALTHY','environment':os.getenv('VERCEL_ENV','local')},
            'social':{'status':'ATTENTION' if source_stale or delivery_attention else 'HEALTHY','account':'@SmartBetSports','schedulerEnabled':social_marketing.enabled('SOCIAL_SCHEDULER_ENABLED'),'autoPublish':social_marketing.enabled('SOCIAL_AUTO_PUBLISH'),'dryRun':os.getenv('DRY_RUN','true').lower()!='false','nextRunAt':_next_daily(at),'latestSourceAt':source_row['verified_at'],'sourceAgeMinutes':round(source_age/60,1),'postCounts':post_counts},
        },
        'experiments':{'regular':regular,'preseason':preseason,'marketCoverage':{'covered':source['coverage_games'],'total':regular['scheduled']}},
        'buyers':buyers,'recentPosts':posts,'feed':feed[:30],
        'definitions':{
            'members':'Active or trialing memberships persisted from verified Stripe events.',
            'marketCoverage':'Games with at least one verified pregame market observation.',
            'socialFreshness':'Age of the newest signed aggregate source; publication blocks after 36 hours.',
        },
    }
