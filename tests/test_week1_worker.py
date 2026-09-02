"""No live providers, production database connections, task registration, or X calls."""
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path

import pytest
from backtesting import week1_worker as worker
from backtesting import capture_nfl_game_markets as capture
from backtesting import nfl_week_workflow as week
from backtesting.nfl_game_predictor import GameProjection


@pytest.fixture
def store(tmp_path):
    at=datetime(2030,9,1,12,tzinfo=timezone.utc)
    game=dict(game_id='game',home_team='BUF',away_team='MIA',kickoff_time=capture.iso(at+timedelta(days=1)),status='scheduled')
    db=tmp_path/'capture.db'
    capture.create(db,season=2030,phase='regular',week=1,games=[game],clock=lambda:at)
    history=tmp_path/'history.json';history.write_text('[]')
    class Model:
        def project(self,*args):return GameProjection(30,10,20,40,60,capture.iso(at-timedelta(days=1)),{},week.MODEL,10,10,.8)
    week.freeze(db,clock=lambda:at,schedule=[game],history_path=history,predictor=Model())
    return db,at,tmp_path/'runtime'


def test_single_instance_and_release(tmp_path):
    db=tmp_path/'capture.db'
    with worker.single_instance(db):
        assert worker.locked(db)
        with pytest.raises(RuntimeError,match='ALREADY_RUNNING'):
            with worker.single_instance(db):pass
    assert not worker.locked(db)


def test_watch_restart_has_no_duplicate_captures_or_wagers(store):
    db,at,runtime=store
    before=week.metrics(db,clock=lambda:at)['prediction_hash']
    attempts=[]
    def tick(path):
        attempts.append(path)
        return capture.tick(path,clock=lambda:at-timedelta(days=1),api_key='mock',fetcher=lambda *a,**k:pytest.fail('Not due; provider must not be contacted'))
    for _ in range(2):
        def sleep(_): (runtime/'STOP').touch()
        assert worker.watch(db,runtime=runtime,tick=tick,qualify=lambda p:week.qualify(p,clock=lambda:at),
                            settle=lambda p:pytest.fail('No pregame ESPN polling'),clock=lambda:at.timestamp(),sleep=sleep)==0
    assert len(attempts)==2
    with capture.connect(db,readonly=True) as c:
        assert c.execute('SELECT COUNT(*) FROM requests').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM forward_wagers').fetchone()[0]==0
    assert week.metrics(db,clock=lambda:at)['prediction_hash']==before
    assert json.loads((runtime/'health.json').read_text())['state']=='STOPPED'


def test_crash_records_health_and_releases_lock(store):
    db,at,runtime=store
    def fail(_):raise ValueError('mock integrity failure')
    with pytest.raises(ValueError):worker.watch(db,runtime=runtime,tick=fail,clock=lambda:at.timestamp())
    assert not worker.locked(db)
    health=json.loads((runtime/'health.json').read_text())
    assert health['state']=='FAILED' and 'mock integrity failure' not in json.dumps(health)


def test_missing_database_never_created(tmp_path):
    path=tmp_path/'missing.db'
    with pytest.raises(FileNotFoundError):worker.preflight(path)
    assert not path.exists()


def test_wrong_experiment_fails_closed(store,monkeypatch):
    db,at,_=store
    monkeypatch.setenv('DATABASE_URL',os.environ['DATABASE_URL'])
    with pytest.raises(ValueError,match='FROZEN_WEEK1'):worker.preflight(db,require_key=False)


def test_local_config_persistent_and_publishing_forced_off(tmp_path,monkeypatch):
    for key in ('SOCIAL_DATABASE_URL','SOCIAL_SOURCE_SIGNING_KEY','SOCIAL_CAMPAIGN_START','DRY_RUN','SOCIAL_AUTO_PUBLISH','SOCIAL_SCHEDULER_ENABLED'):
        monkeypatch.setenv(key,'unsafe')
    worker.configure_local(create=True,root=tmp_path)
    key=os.environ['SOCIAL_SOURCE_SIGNING_KEY']
    worker.configure_local(create=True,root=tmp_path)
    assert os.environ['SOCIAL_SOURCE_SIGNING_KEY']==key and len(key)>=32
    assert os.environ['DRY_RUN']=='true' and os.environ['SOCIAL_AUTO_PUBLISH']=='false'
    assert str(tmp_path).replace('\\','/') in os.environ['SOCIAL_DATABASE_URL']


def test_pytest_cannot_load_real_local_secrets():
    with pytest.raises(ValueError,match='temporary'):worker.configure_local()


def test_task_script_uses_venv_single_instance_and_login():
    script=(worker.ROOT/'tools/week1-task.ps1').read_text()
    for required in ('.venv\\Scripts\\python.exe','IgnoreNew','-AtLogOn','-LogonType Interactive','-WindowStyle Hidden','-WorkingDirectory $repo','week1_worker setup'):
        assert required in script
    assert 'Stop-ScheduledTask' not in script
    assert 'publish-one' not in script


def test_watch_cli_uses_shared_guard(store,monkeypatch):
    db,_,_=store
    calls=[]
    monkeypatch.setattr(worker,'watch',lambda *a,**k:calls.append((a,k)) or 0)
    assert week.main(['watch','--db',str(db),'--allow-paid'])==0
    assert calls[0][0]==(db,)


def test_preflight_pins_hashes_and_requires_key(store,monkeypatch):
    db,_,_=store
    monkeypatch.setenv('DATABASE_URL',os.environ['DATABASE_URL'])
    monkeypatch.setenv('THE_ODDS_API_KEY','')
    result=dict(experiment_id='NFL-2026-REG1-v1',predictions=16,manifest_hash=worker.MANIFEST,prediction_hash=worker.PREDICTIONS)
    monkeypatch.setattr(week,'metrics',lambda p:result)
    with pytest.raises(ValueError,match='KEY_MISSING'):worker.preflight(db)
    monkeypatch.setenv('THE_ODDS_API_KEY','mock-not-live')
    assert worker.preflight(db)==result
    result['prediction_hash']='changed'
    with pytest.raises(ValueError,match='INTEGRITY_FAILED'):worker.preflight(db)


def test_second_worker_does_not_clear_graceful_stop(store):
    db,_,runtime=store
    runtime.mkdir();stop=runtime/'STOP';stop.touch()
    with worker.single_instance(db):
        with pytest.raises(RuntimeError,match='ALREADY_RUNNING'):
            worker.watch(db,runtime=runtime,tick=lambda p:pytest.fail('duplicate tick'))
        assert stop.exists()


def test_daily_draft_failure_does_not_block_capture(store,monkeypatch):
    db,at,runtime=store
    def fail(p):raise ValueError('mock missing social source')
    monkeypatch.setattr(worker,'daily_draft',fail)
    worker.watch(db,runtime=runtime,local_drafts=True,tick=lambda p:{'state':'NOT_DUE'},qualify=lambda p:{'new_qualified_wagers':0},
                 clock=lambda:at.timestamp(),sleep=lambda seconds:(runtime/'STOP').touch())
    health=json.loads((runtime/'health.json').read_text())
    assert health['state']=='STOPPED' and health['social']['state']=='DRAFT_BLOCKED_REVIEW_CONFIG_OR_SOURCE'
