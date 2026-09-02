import json
import sqlite3
from datetime import datetime,timezone,timedelta

import pytest
from backtesting import capture_nfl_game_markets as capture
from backtesting import nfl_week_workflow as week
from backtesting.nfl_game_predictor import GameProjection
from nfl_providers import HttpJsonResponse


@pytest.fixture
def experiment(tmp_path):
    class Clock:
        at=datetime(2030,9,1,12,tzinfo=timezone.utc)
        def __call__(self):return self.at
    clock=Clock()
    game=dict(game_id='game',home_team='BUF',away_team='MIA',kickoff_time=capture.iso(clock()+timedelta(days=1)),status='scheduled')
    path=tmp_path/'regular.db'
    capture.create(path,season=2030,phase='regular',week=1,games=[game],clock=clock)
    history=tmp_path/'history.json'
    history.write_text(json.dumps([dict(completed_at=capture.iso(clock()-timedelta(days=2)),data_as_of=capture.iso(clock()-timedelta(days=1)))]))
    class Model:
        def project(self,*args):return GameProjection(30,10,20,40,60,capture.iso(clock()-timedelta(days=1)),{},week.MODEL,10,10,.8)
    return path,clock,game,history,Model()


def freeze(e):return week.freeze(e[0],clock=e[1],schedule=[e[2]],history_path=e[3],predictor=e[4])


def test_freeze_once_and_provenance(experiment):
    path,clock,*_=experiment
    assert freeze(experiment)['inserted']==1
    assert freeze(experiment)['inserted']==0
    with capture.connect(path,clock) as c:
        p=c.execute('SELECT * FROM forward_predictions').fetchone()
        assert week.verify_prediction(p)[1]['available_input_hash']
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):c.execute("UPDATE forward_predictions SET probability=.99")
    assert week.metrics(path,clock)['predictions']==1


def test_bad_mapping_blocks_freezing(experiment):
    path,clock,g,history,model=experiment
    with pytest.raises(ValueError,match='identity'):
        week.freeze(path,clock=clock,schedule=[dict(g,home_team='NE')],history_path=history,predictor=model)


def test_late_freeze_blocked(experiment):
    experiment[1].at+=timedelta(hours=23)
    result=freeze(experiment)
    assert result['inserted']==0 and result['unavailable'][0]['reason']=='FREEZE_DEADLINE_PASSED'


def test_future_inputs_filtered(experiment):
    path,clock,g,history,_=experiment
    history.write_text(json.dumps([dict(completed_at=capture.iso(clock()+timedelta(hours=1)),data_as_of=capture.iso(clock()+timedelta(hours=2)))]))
    class Model:
        def project(self,g,rows):assert rows==[];return None
    assert week.freeze(path,clock=clock,schedule=[g],history_path=history,predictor=Model())['inserted']==0


def test_no_missing_price_or_postgame_wager(experiment):
    path,clock,g,*_=experiment
    freeze(experiment)
    assert week.qualify(path,clock)['new_qualified_wagers']==0
    clock.at+=timedelta(hours=25)
    assert week.qualify(path,clock)['new_qualified_wagers']==0
    week.settle(path,schedule=[dict(g,status='final',home_score=20,away_score=10)],clock=clock)
    result=week.metrics(path,clock)
    assert result['winner_record']==dict(WIN=1,LOSS=0,PUSH=0)
    assert result['profiles']['BALANCED']['roi'] is None
    assert week.settle(path,schedule=[dict(g,status='final',home_score=20,away_score=10)],clock=clock)['new_finals']==0


def test_pregame_qualification_then_grade(experiment):
    path,clock,g,*_=experiment
    freeze(experiment)
    payload=[dict(id='odds',home_team='BUF',away_team='MIA',commence_time=g['kickoff_time'],bookmakers=[dict(key='book',markets=[dict(key='h2h',last_update=capture.iso(clock()),outcomes=[dict(name='BUF',price=-110),dict(name='MIA',price=-110)])])])]
    capture.tick(path,clock=clock,api_key='fake',fetcher=lambda *a,**k:HttpJsonResponse(payload,200,{'x-requests-remaining':'100'}))
    assert week.qualify(path,clock)['new_qualified_wagers']==3
    assert week.qualify(path,clock)['new_qualified_wagers']==0
    clock.at+=timedelta(hours=25)
    week.settle(path,schedule=[dict(g,status='final',home_score=10,away_score=10)],clock=clock)
    result=week.metrics(path,clock)
    assert result['winner_record']['PUSH']==1
    assert result['profiles']['BALANCED']['record']['PUSH']==1
    assert result['profiles']['BALANCED']['units']==0


def test_final_correction_not_silent(experiment):
    path,clock,g,*_=experiment
    freeze(experiment);clock.at+=timedelta(hours=25)
    week.settle(path,schedule=[dict(g,status='final',home_score=20,away_score=10)],clock=clock)
    with pytest.raises(ValueError,match='correction'):
        week.settle(path,schedule=[dict(g,status='final',home_score=0,away_score=10)],clock=clock)


def test_preflight_one_request_no_markets(experiment):
    path,clock,g,*_=experiment
    freeze(experiment);calls=[]
    def fetch(*a,**k):calls.append(1);return HttpJsonResponse([],200,{'x-requests-remaining':'97','x-requests-used':'3'})
    assert week.preflight(path,api_key='fake',fetcher=fetch,clock=clock)['credits_used']==3
    assert week.preflight(path,api_key='fake',fetcher=fetch,clock=clock)['state']=='ALREADY_ATTEMPTED'
    assert len(calls)==1
    assert capture.report(path,clock=clock)['games_with_any_market']==0


def test_preseason_rejected(tmp_path):
    path=tmp_path/'preseason.db'
    clock=lambda:datetime(2030,9,1,tzinfo=timezone.utc)
    capture.create(path,season=2030,phase='preseason',week=1,games=[dict(game_id='g',home_team='BUF',away_team='MIA',kickoff_time='2030-09-03T00:00:00Z')],clock=clock)
    with pytest.raises(ValueError,match='preseason'):week.initialize(path,clock)


def test_cumulative_does_not_double_count(experiment):
    freeze(experiment)
    result=week.season_metrics([experiment[0]],experiment[1])
    assert result['phase']=='regular' and result['predictions']==1
    with pytest.raises(ValueError,match='Overlapping'):week.season_metrics([experiment[0],experiment[0]],experiment[1])


def test_readiness_warns_without_live_provider(experiment):
    freeze(experiment)
    report=week.readiness(experiment[0],experiment[1])
    assert report['checks']['live_provider_preflight']=='WARN'
    assert report['checks']['frozen_predictions']=='PASS'
    assert report['readiness']=='WARN'


def test_tampering_detected_without_regeneration(experiment):
    freeze(experiment)
    with capture.connect(experiment[0],experiment[1]) as c:
        c.execute('DROP TRIGGER forward_predictions_no_update')
        c.execute("UPDATE forward_predictions SET winner='MIA'")
    with pytest.raises(ValueError,match='integrity'):week.metrics(experiment[0],experiment[1])
