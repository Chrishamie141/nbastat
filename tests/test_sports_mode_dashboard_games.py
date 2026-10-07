from datetime import datetime, timezone
import pytest
from backend.app.services.sports_mode_service import get_sports_mode
from backend.app.services.game_watchability_service import score_game
from backend.app.services import schedule_service

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


def _event(state, *, completed=False, home_score=None, away_score=None, detail=None):
    return {"id": "score-test", "date": "2026-10-20T23:00:00Z", "season": {"year": 2026, "type": 2},
            "status": {"type": {"state": state, "completed": completed, "detail": detail or state}},
            "competitions": [{"status": {"type": {"state": state, "completed": completed, "detail": detail or state}},
                              "competitors": [
                                  {"homeAway": "home", "score": home_score, "team": {"id": "1", "abbreviation": "BOS", "displayName": "Boston Celtics"}},
                                  {"homeAway": "away", "score": away_score, "team": {"id": "2", "abbreviation": "NYK", "displayName": "New York Knicks"}},
                              ]}]}


def test_dashboard_schedule_preserves_authoritative_final_scores_and_winner(monkeypatch):
    schedule_service._CACHE.clear()
    monkeypatch.setattr(schedule_service, "_fetch_espn", lambda *_args: [_event("post", completed=True, home_score="110", away_score="103", detail="Final")])
    rows = schedule_service.upcoming_games(["nba"], include_completed=True)
    assert rows[0].status == "final"
    assert rows[0].homeScore == 110 and rows[0].awayScore == 103
    assert rows[0].homeTeam.abbreviation == "BOS" and rows[0].awayTeam.abbreviation == "NYK"


def test_dashboard_schedule_final_missing_score_remains_missing(monkeypatch, caplog):
    schedule_service._CACHE.clear()
    monkeypatch.setattr(schedule_service, "_fetch_espn", lambda *_args: [_event("post", completed=True, detail="Final")])
    row = schedule_service.upcoming_games(["nba"], include_completed=True)[0]
    assert row.status == "final" and row.homeScore is None and row.awayScore is None
    assert "final_game_score_missing" in caplog.text


@pytest.mark.parametrize("state,detail,expected", [
    ("pre", "Scheduled", "scheduled"), ("in", "In Progress", "live"),
    ("pre", "Postponed", "postponed"), ("pre", "Canceled", "canceled"),
])
def test_dashboard_schedule_lifecycle_does_not_invent_final_score(monkeypatch, state, detail, expected):
    schedule_service._CACHE.clear()
    monkeypatch.setattr(schedule_service, "_fetch_espn", lambda *_args: [_event(state, detail=detail)])
    row = schedule_service.upcoming_games(["nba"], include_completed=True)[0]
    assert row.status == expected and row.homeScore is None and row.awayScore is None
