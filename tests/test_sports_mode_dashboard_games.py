from datetime import datetime, timezone
from backend.app.services.sports_mode_service import get_sports_mode
from backend.app.services.game_watchability_service import score_game

def test_manual_override(monkeypatch):
    monkeypatch.setenv('SPORTS_MODE_OVERRIDE','nfl')
    m=get_sports_mode(datetime(2026,7,18,tzinfo=timezone.utc))
    assert m.mode=='nfl' and m.overrideActive is True

def test_offseason_calendar(monkeypatch):
    monkeypatch.setenv('SPORTS_MODE_OVERRIDE','auto')
    monkeypatch.setattr('backend.app.services.sports_mode_service.upcoming_games', lambda *a, **k: [])
    m=get_sports_mode(datetime(2026,7,1,tzinfo=timezone.utc))
    assert m.mode=='offseason'

def test_nfl_calendar_lead(monkeypatch):
    monkeypatch.setenv('SPORTS_MODE_OVERRIDE','auto')
    monkeypatch.setattr('backend.app.services.sports_mode_service.upcoming_games', lambda *a, **k: [])
    m=get_sports_mode(datetime(2026,7,18,tzinfo=timezone.utc))
    assert 'nfl' in m.activeLeagues

def test_watchability_deterministic():
    game={'status':'scheduled','broadcast':['ESPN'],'venue':'Example','startTimeUtc':datetime(2026,8,10,1,tzinfo=timezone.utc)}
    assert score_game(game)==score_game(game)
    assert 'National broadcast' in score_game(game)['watchReasons']
