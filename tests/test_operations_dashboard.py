from datetime import datetime, timezone


def test_command_center_reconciles_social_experiment_and_buyer_metrics(tmp_path,monkeypatch):
    from backend.app.database import get_db_connection
    from backend.app.services import social_marketing as social
    from backend.app.services.operations_dashboard_service import command_center
    at=datetime(2030,9,1,13,30,tzinfo=timezone.utc)
    monkeypatch.setenv('SOCIAL_DATABASE_URL','sqlite:///'+(tmp_path/'operations.db').as_posix())
    monkeypatch.setenv('SOCIAL_SOURCE_SIGNING_KEY','test-signing-key-not-production-123456')
    monkeypatch.setenv('SOCIAL_CAMPAIGN_START','2030-09-01')
    monkeypatch.setenv('SOCIAL_SCHEDULER_ENABLED','true')
    monkeypatch.setenv('SOCIAL_AUTO_PUBLISH','true')
    monkeypatch.setenv('DRY_RUN','false')
    social.initialize()
    source={'verified_at':at.isoformat(),'preseason':{'record':{'WIN':11,'LOSS':4,'PUSH':1},'predictions':16,'qualified_wagers':0},
            'regular':{'season':2026,'week':1,'predictions':16,'scheduled':16,'graded':4,'winner_record':{'WIN':3,'LOSS':1,'PUSH':0}},
            'coverage_games':12,'claims_policy':'predictions_are_not_wagers;small_sample_not_future_performance'}
    social.store_source(source)
    with get_db_connection() as c:
        c.execute("INSERT INTO users(name,email,password_hash,created_at,updated_at,is_active,subscription_plan,subscription_status,access_source) VALUES(?,?,?,?,?,1,'founding','active','stripe_paid')",('Operator','operator@example.com','hash',at.isoformat(),at.isoformat()))
    result=command_center(lambda:at)
    assert result['overallStatus']=='ATTENTION'
    assert result['experiments']['marketCoverage']=={'covered':12,'total':16}
    assert result['buyers']['members']==1 and result['buyers']['paid_members']==1
    assert result['systems']['social']['nextRunAt']=='2030-09-02T13:00:00+00:00'
    assert any(alert['code']=='MARKET_COVERAGE_INCOMPLETE' for alert in result['alerts'])
    assert {item['type'] for item in result['feed']}=={'data','experiment'}


def test_command_center_route_requires_internal_access():
    from backend.app.main import app
    route=app.openapi()['paths']['/api/internal/operations']['get']
    assert route['responses']['200']['description']=='Successful Response'
