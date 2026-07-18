from backend.app.services.team_metadata import teams_for_league
from backend.app.services.game_watchability_service import score_game


def test_nfl_teams_are_canonical_with_logos():
    teams = teams_for_league('nfl')
    giants = next(t for t in teams if t.abbreviation == 'NYG')
    assert giants.name == 'New York Giants'
    assert giants.abbreviation == giants.abbreviation.upper()
    assert giants.logoUrl and 'espncdn.com' in giants.logoUrl


def test_watch_score_internal_still_calculates_without_detail_reason():
    result = score_game({'status':'scheduled','broadcast':['ESPN'],'nationalBroadcast':True,'venue':'Stadium'})
    assert result['watchScore'] > 0
    assert 'Schedule details available' not in result['watchReasons']
