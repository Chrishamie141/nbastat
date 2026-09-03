import json
from datetime import datetime,timezone,timedelta
from concurrent.futures import ThreadPoolExecutor

import pytest
from backend.app.services import social_marketing as social


@pytest.fixture
def setup(tmp_path,monkeypatch):
    clock=lambda:datetime(2030,9,1,13,tzinfo=timezone.utc)
    monkeypatch.setenv('SOCIAL_DATABASE_URL','sqlite:///'+(tmp_path/'social.db').as_posix())
    monkeypatch.setenv('SOCIAL_SOURCE_SIGNING_KEY','test-signing-key-not-production-123456')
    monkeypatch.setenv('SOCIAL_CAMPAIGN_START','2030-09-01')
    monkeypatch.setenv('DRY_RUN','true');monkeypatch.setenv('SOCIAL_AUTO_PUBLISH','false')
    monkeypatch.delenv('VERCEL',raising=False);monkeypatch.delenv('VERCEL_ENV',raising=False)
    monkeypatch.delenv('SOCIAL_CTA_URL',raising=False)
    social.initialize()
    source=dict(verified_at=clock().isoformat(),preseason=dict(record=dict(WIN=11,LOSS=4,PUSH=1),predictions=16,qualified_wagers=0),
                regular=dict(week=1,predictions=16,scheduled=16,graded=0,winner_record=dict(WIN=0,LOSS=0,PUSH=0)),coverage_games=0)
    seed(source)
    return clock,source


def seed(source):
    with social.connection() as c:
        c.execute('INSERT INTO social_sources VALUES(?,?,?,?)',(social.sha(source),social.encode(source),social.sign(source),source['verified_at']))


def test_dry_run_default_and_numeric_provenance(setup):
    clock,_=setup
    post=social.generate(clock)
    assert '11-4-1' in post['content'] and '0 qualified wagers' in post['content']
    assert '73%' not in post['content']
    assert social.publish_one(post['post_id'],clock,lambda:pytest.fail('X called'))['status']=='DRY_RUN'
    assert social.generate(clock)['post_id']==post['post_id']


def test_seven_day_previews_use_current_evidence_and_never_enqueue(setup):
    from backtesting.social_marketing import preview_week
    clock,source=setup
    result=preview_week(clock)
    assert not result['published'] and len(result['previews'])==7
    assert all(p['data_as_of']==source['verified_at'] for p in result['previews'])
    assert all(p['status']=='PREVIEW_ONLY' for p in result['previews'])
    assert '11-4-1' in result['previews'][0]['content']
    with social.connection() as c:
        assert c.execute('SELECT COUNT(*) FROM social_posts').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM social_publish_days').fetchone()[0]==0


def test_preview_rejects_stale_evidence(setup):
    from backtesting.social_marketing import preview_week
    with pytest.raises(ValueError,match='stale'):preview_week(lambda:setup[0]()+timedelta(days=2))


def test_twenty_eight_templates_distinct_valid_length(setup):
    _,source=setup
    posts=[social.render(day,source)[1] for day in range(28)]
    assert len(set(posts))==28 and all(len(post)<=280 for post in posts)
    for i,left in enumerate(posts):
        for right in posts[i+1:]:
            assert social.SequenceMatcher(None,social.similarity(left),social.similarity(right)).ratio()<.82


def test_stale_source_blocks(setup):
    clock,_=setup
    with pytest.raises(ValueError,match='stale'):social.generate(lambda:clock()+timedelta(days=2))


def test_tampered_source_blocks(setup):
    with social.connection() as c:c.execute("UPDATE social_sources SET payload='{}'")
    with pytest.raises(ValueError,match='authenticity'):social.generate(setup[0])


class Response:
    status_code=201
    def json(self):return {'data':{'id':'12345'}}


def enable(monkeypatch):
    monkeypatch.setenv('DRY_RUN','false');monkeypatch.setenv('SOCIAL_AUTO_PUBLISH','true')


def test_publish_once_under_concurrency(setup,monkeypatch):
    clock,_=setup;post=social.generate(clock);enable(monkeypatch);calls=[]
    class Client:
        def verify_company(self):pass
        def post(self,text):calls.append(text);return Response()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:social.publish_one(post['post_id'],clock,Client),range(2)))
    assert len(calls)==1 and sum(r['published'] for r in results)==1


def test_uncertain_delivery_never_retries(setup,monkeypatch):
    clock,_=setup;post=social.generate(clock);enable(monkeypatch)
    class Client:
        def verify_company(self):pass
        def post(self,text):raise TimeoutError('secret-token')
    assert social.publish_one(post['post_id'],clock,Client)['status']=='UNKNOWN'
    assert social.publish_one(post['post_id'],clock,lambda:pytest.fail('retry'))['status']=='UNKNOWN'
    assert 'secret-token' not in json.dumps(social.campaign_report())


def test_wrong_company_account_stops_posting(setup,monkeypatch):
    clock,_=setup;post=social.generate(clock);enable(monkeypatch)
    class Client:
        def verify_company(self):raise ValueError('wrong')
        def post(self,text):pytest.fail('wrong account posted')
    assert social.publish_one(post['post_id'],clock,Client)['status']=='FAILED'


def test_tampered_post_blocked(setup,monkeypatch):
    clock,_=setup;post=social.generate(clock);enable(monkeypatch)
    with social.connection() as c:c.execute('UPDATE social_posts SET content=? WHERE post_id=?',('We win 99% of bets',post['post_id']))
    with pytest.raises(ValueError,match='authenticity'):
        social.publish_one(post['post_id'],clock,lambda:pytest.fail('tampered post'))


def test_preview_cannot_publish(setup,monkeypatch):
    post=social.generate(setup[0]);enable(monkeypatch);monkeypatch.setenv('VERCEL_ENV','preview')
    with pytest.raises(ValueError,match='preview'):social.publish_one(post['post_id'],setup[0])


def test_dry_run_command_ignores_publish_flags(setup,monkeypatch,capsys):
    from backtesting.social_marketing import main
    enable(monkeypatch)
    monkeypatch.setattr(social,'generate',lambda:{'status':'DRAFT'})
    monkeypatch.setattr(social,'publish_one',lambda *a,**k:pytest.fail('publish called'))
    assert main(['dry-run'])==0


def test_sqlite_forbidden_on_vercel(setup,monkeypatch):
    monkeypatch.setenv('VERCEL','1')
    with pytest.raises(ValueError,match='durable'):social.initialize()


def test_production_default_refused(setup,monkeypatch):
    monkeypatch.setenv('SOCIAL_DATABASE_URL','sqlite:///'+social.PRODUCTION_DATABASE.as_posix())
    with pytest.raises(ValueError,match='predictions.db'):social.initialize()


def test_cron_auth_and_disabled(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.app.api.social_cron import router
    app=FastAPI();app.include_router(router);client=TestClient(app)
    monkeypatch.setenv('CRON_SECRET','private-cron')
    monkeypatch.setenv('SOCIAL_SCHEDULER_ENABLED','false')
    assert client.get('/api/cron/social-daily').status_code==401
    assert client.get('/api/cron/social-daily',headers={'authorization':'Bearer private-cron'}).json()['status']=='DISABLED'


def test_official_client_only_expected_api_endpoints(setup,monkeypatch):
    for name in ('X_API_KEY','X_API_SECRET','X_ACCESS_TOKEN','X_ACCESS_TOKEN_SECRET'):monkeypatch.setenv(name,'fake')
    monkeypatch.setenv('X_EXPECTED_USER_ID','12345')
    calls=[]
    class Session:
        auth=None
        def get(self,url,**kwargs):calls.append(url);r=Response();r.status_code=200;return r
        def post(self,url,**kwargs):calls.append(url);assert set(kwargs['json'])=={'text'};return Response()
    monkeypatch.setattr(social.requests,'Session',Session)
    client=social.OfficialX();client.verify_company();client.post('text')
    assert calls==['https://api.x.com/2/users/me','https://api.x.com/2/tweets']


def test_event_guards_missing_market_or_results(setup):
    with pytest.raises(ValueError,match='No verified'):social.generate(setup[0],event='pregame_picks_ready')
    with pytest.raises(ValueError,match='incomplete'):social.generate(setup[0],event='final_weekly_results')


def test_unknown_delivery_can_be_verified_without_reposting(setup,monkeypatch):
    clock,_=setup;post=social.generate(clock);monkeypatch.setenv('X_EXPECTED_USER_ID','company')
    with social.connection() as c:c.execute("UPDATE social_posts SET status='UNKNOWN' WHERE post_id=?",(post['post_id'],))
    class Client:
        def verify_company(self):pass
        def lookup(self,pid):
            class Remote:
                status_code=200
                def json(self):return {'data':{'id':pid,'author_id':'company','text':post['content']}}
            return Remote()
        def post(self,text):pytest.fail('repost')
    assert social.reconcile_published(post['post_id'],'12345',clock,Client)['reconciled']


def test_cta_domain_is_explicit(setup,monkeypatch):
    monkeypatch.setenv('SOCIAL_CTA_URL','https://wrong.example/subscribe')
    monkeypatch.setenv('SOCIAL_COMPANY_DOMAIN','company.example')
    with pytest.raises(ValueError,match='match'):social.generate(setup[0])


def test_cta_is_limited_to_two_posts_per_week(setup,monkeypatch):
    _,source=setup
    monkeypatch.setenv('SOCIAL_CTA_URL','https://smartbetsports.com')
    monkeypatch.setenv('SOCIAL_COMPANY_DOMAIN','smartbetsports.com')
    posts=[social.render(day,source)[1] for day in range(28)]
    assert sum('https://smartbetsports.com' in post for post in posts)==8
    assert all(len(post)<=280 for post in posts)


def test_signed_remote_source_acceptance_is_idempotent(setup):
    clock,source=setup
    source=dict(source,claims_policy='predictions_are_not_wagers;small_sample_not_future_performance')
    envelope={'source_id':social.sha(source),'payload':source,'signature':social.sign(source)}
    first=social.accept_source_envelope(envelope,clock)
    second=social.accept_source_envelope(envelope,clock)
    assert first==second
    with social.connection() as c:
        assert c.execute('SELECT COUNT(*) FROM social_sources').fetchone()[0]==2
    envelope['signature']='0'*64
    with pytest.raises(ValueError,match='authenticity'):social.accept_source_envelope(envelope,clock)


def test_campaign_continues_with_dated_rotation_after_twenty_eight_days(setup):
    clock,source=setup
    future=clock()+timedelta(days=28)
    source['verified_at']=future.isoformat();seed(source)
    post=social.generate(lambda:future)
    assert post['category'].endswith('|28') and post['content'].startswith('Sep 29 research update:')


def test_campaign_refuses_dates_before_start(setup):
    with pytest.raises(ValueError,match='not started'):social.generate(lambda:setup[0]()-timedelta(days=1))


def test_unknown_vercel_job_error_redacted(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.app.api.social_cron import router
    app=FastAPI();app.include_router(router);client=TestClient(app)
    monkeypatch.setenv('CRON_SECRET','private-cron');monkeypatch.setenv('SOCIAL_SCHEDULER_ENABLED','true')
    def fail():raise RuntimeError('secret-token')
    monkeypatch.setattr(social,'daily',fail)
    response=client.get('/api/cron/social-daily',headers={'authorization':'Bearer private-cron'})
    assert response.status_code==503 and 'secret-token' not in response.text


def test_remote_source_route_requires_separate_secret(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.app.api.social_cron import router
    app=FastAPI();app.include_router(router);client=TestClient(app)
    monkeypatch.setenv('SOCIAL_SYNC_SECRET','sync-secret')
    monkeypatch.setattr(social,'accept_source_envelope',lambda body:{'accepted':body['source_id']})
    assert client.post('/api/cron/social-source',json={'source_id':'x'}).status_code==401
    response=client.post('/api/cron/social-source',json={'source_id':'x'},headers={'authorization':'Bearer sync-secret'})
    assert response.json()=={'accepted':'x'}


def test_source_sync_task_never_publishes_or_opens_prediction_db_for_writes():
    from pathlib import Path
    script=(Path(__file__).resolve().parents[1]/'tools/social-source-task.ps1').read_text(encoding='utf-8')
    assert 'remote-sync' in script
    assert 'publish-one' not in script and 'SOCIAL_AUTO_PUBLISH' not in script
    assert "New-ScheduledTaskTrigger -Daily -At '8:30 AM'" in script
