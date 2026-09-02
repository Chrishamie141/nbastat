import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from backtesting import capture_nfl_game_markets as capture
from nfl_providers import HttpJsonResponse, StructuredHttpError


class Clock:
    def __init__(self):
        self.now = datetime(2030, 9, 1, 18, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


@pytest.fixture
def setup(tmp_path):
    clock = Clock()
    game = dict(game_id='espn-test-next', home_team='BUF', away_team='MIA',
                kickoff_time=capture.iso(clock() + timedelta(hours=24)), status='scheduled')
    path = tmp_path / 'next-capture.db'
    capture.create(path, season=2030, phase='regular', week=1, games=[game], clock=clock)
    return path, clock, game


def event(game, clock, markets=capture.MARKETS):
    return dict(id='odds-event-1', sport_key='americanfootball_nfl', home_team='Buffalo Bills',
                away_team='Miami Dolphins', commence_time=game['kickoff_time'], bookmakers=[dict(
                    key='test-book', title='Test Book', last_update=capture.iso(clock()), markets=[dict(
                        key=kind, last_update=capture.iso(clock()), outcomes=(
                            [dict(name='Over', price=-110, point=45.5), dict(name='Under', price=-110, point=45.5)]
                            if kind == 'totals' else
                            [dict(name='Buffalo Bills', price=-110, point=-3), dict(name='Miami Dolphins', price=-110, point=3)]
                            if kind == 'spreads' else
                            [dict(name='Buffalo Bills', price=-150), dict(name='Miami Dolphins', price=130)])) for kind in markets])])


def response(game, clock, **kwargs):
    return HttpJsonResponse([event(game, clock, **kwargs)], 200,
                            {'x-requests-remaining': '100', 'x-requests-used': '3', 'x-requests-last': '3'})


def run(setup, fetcher=None):
    path, clock, game = setup
    return capture.tick(path, api_key='secret-test-key', fetcher=fetcher or (lambda *a, **k: response(game, clock)), clock=clock)


def test_capture_complete_provenance_and_idempotency(setup):
    path, clock, game = setup
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        return response(game, clock)
    assert run(setup, fetch)['games'][game['game_id']] == 'COMPLETE'
    assert run(setup, fetch)['state'] == 'IDLE'
    assert len(calls) == 1
    assert 'markets=h2h%2Cspreads%2Ctotals' in calls[0] and 'regions=us' in calls[0]
    report = capture.report(path, clock=clock)
    assert report['credits_reserved'] == 3
    assert report['games_with_any_market'] == report['complete_checkpoints'] == 1
    with capture.connect(path, clock) as connection:
        rows = connection.execute('SELECT * FROM captures').fetchall()
        assert len(rows) == 3
        assert connection.execute('SELECT COUNT(*) FROM quotes').fetchone()[0] == 6
        for row in rows:
            assert row['retrieved_at'] and row['stored_at'] and row['source_time']
            assert row['bookmaker'] == 'test-book'
            assert len(json.loads(row['outcomes'])) == 2
            assert json.loads(row['provenance'])['provider'] == 'the-odds-api'
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            connection.execute('DELETE FROM captures')
    assert 'secret-test-key' not in json.dumps(report)


def test_many_games_one_request(tmp_path):
    clock = Clock()
    games = [dict(game_id='g1', home_team='BUF', away_team='MIA', kickoff_time=capture.iso(clock()+timedelta(days=1))),
             dict(game_id='g2', home_team='NYJ', away_team='NE', kickoff_time=capture.iso(clock()+timedelta(days=1)))]
    path = tmp_path/'broad.db'
    capture.create(path, season=2030, phase='regular', week=1, games=games, clock=clock)
    second = event(games[1], clock)
    second.update(id='odds-event-2', home_team='NYJ', away_team='NE')
    for market in second['bookmakers'][0]['markets']:
        if market['key'] != 'totals':
            market['outcomes'][0]['name'], market['outcomes'][1]['name'] = 'NYJ', 'NE'
    calls=[]
    def fetch(*a, **k):
        calls.append(1)
        return HttpJsonResponse([event(games[0], clock), second], 200, {'x-requests-remaining':'100'})
    result = capture.tick(path, api_key='x', fetcher=fetch, clock=clock)
    assert list(result['games'].values()) == ['COMPLETE', 'COMPLETE']
    assert len(calls)==1


@pytest.mark.parametrize('delay', [24*3600, 24*3600+1])
def test_response_at_or_after_kickoff_writes_no_markets(setup, delay):
    path, clock, game = setup
    payload = response(game, clock)
    def fetch(*a, **k):
        clock.now += timedelta(seconds=delay)
        return payload
    assert run(setup, fetch)['games'][game['game_id']] == 'KICKOFF_BLOCKED'
    with capture.connect(path, clock) as connection:
        assert connection.execute('SELECT COUNT(*) FROM captures').fetchone()[0] == 0


def test_no_request_at_kickoff_and_missed_status(setup):
    path, clock, game = setup
    clock.now += timedelta(days=1)
    assert run(setup, lambda *a, **k: pytest.fail('network'))['state'] == 'IDLE'
    assert {r['state'] for r in capture.report(path, clock=clock)['checkpoints']} == {'MISSED_CAPTURE'}


@pytest.mark.parametrize('change,expected', [
    ('stale', 'STALE_DATA'), ('future', 'STALE_DATA'), ('missing_time', 'MALFORMED_RESPONSE'),
    ('missing_side', 'MALFORMED_RESPONSE'), ('bad_price', 'MALFORMED_RESPONSE'),
    ('nonfinite', 'MALFORMED_RESPONSE'), ('changed_kickoff', 'SCHEDULE_CHANGED'),
    ('ambiguous', 'AMBIGUOUS_EVENT'), ('empty', 'NO_MARKET'), ('partial', 'PARTIAL')])
def test_unusable_or_partial_market_status(setup, change, expected):
    path, clock, game = setup
    payload = response(game, clock)
    e = payload.payload[0]
    if change == 'stale':
        for m in e['bookmakers'][0]['markets']: m['last_update'] = capture.iso(clock()-timedelta(hours=1))
    elif change == 'future':
        for m in e['bookmakers'][0]['markets']: m['last_update'] = capture.iso(clock()+timedelta(minutes=1))
    elif change == 'missing_time':
        e['bookmakers'][0].pop('last_update')
        for m in e['bookmakers'][0]['markets']: m.pop('last_update')
    elif change == 'missing_side':
        for m in e['bookmakers'][0]['markets']: m['outcomes'].pop()
    elif change in {'bad_price','nonfinite'}:
        for m in e['bookmakers'][0]['markets']: m['outcomes'][0]['price'] = 0 if change=='bad_price' else float('nan')
    elif change == 'changed_kickoff': e['commence_time'] = capture.iso(clock()+timedelta(hours=23))
    elif change == 'ambiguous': payload.payload.append(dict(e))
    elif change == 'empty': payload.payload.clear()
    elif change == 'partial': e['bookmakers'][0]['markets'] = e['bookmakers'][0]['markets'][:1]
    result = run(setup, lambda *a, **k: payload)
    assert result['games'][game['game_id']] == expected


@pytest.mark.parametrize('http,classification,expected', [
    (401, 'AUTHENTICATION_OR_ENTITLEMENT', 'AUTH_ERROR'),
    (403, 'AUTHENTICATION_OR_ENTITLEMENT', 'AUTH_ERROR'),
    (429, 'RATE_LIMITED', 'RATE_LIMITED'),
    (500, 'TRANSIENT_PROVIDER_ERROR', 'UPSTREAM_ERROR'),
    (None, 'NETWORK_ERROR', 'NETWORK_FAILURE'),
    (200, 'INVALID_PROVIDER_RESPONSE', 'MALFORMED_RESPONSE')])
def test_provider_errors_do_not_leak_or_retry(setup, http, classification, expected):
    def fetch(*a, **k):
        raise StructuredHttpError(status=http, classification=classification,
                                  message='secret-test-key', url='https://example.test?apiKey=secret-test-key')
    result=run(setup, fetch)
    assert result['state']==expected
    path, clock, _=setup
    assert capture.report(path, clock=clock)['credits_reserved']==3
    assert 'secret-test-key' not in json.dumps(capture.report(path, clock=clock))
    assert run(setup, lambda *a, **k: pytest.fail('retry'))['state']=='IDLE'


def test_missing_key_no_budget_spend(setup):
    path, clock, _=setup
    assert capture.tick(path, api_key='', clock=clock)['state']=='AUTH_ERROR'
    assert capture.report(path, clock=clock)['credits_reserved']==0


def test_persistent_budget_and_quota_headers(tmp_path):
    clock=Clock()
    game=dict(game_id='g', home_team='BUF', away_team='MIA', kickoff_time=capture.iso(clock()+timedelta(days=1)))
    path=tmp_path/'budget.db'
    capture.create(path, season=2030, phase='regular', week=1, games=[game], credits=3, clock=clock)
    run((path, clock, game))
    clock.now += timedelta(hours=18)
    assert run((path, clock, game), lambda *a, **k: pytest.fail('budget exceeded'))['state']=='BUDGET_EXHAUSTED'


@pytest.mark.parametrize('headers,expected', [({}, 'QUOTA_UNKNOWN'),
    ({'x-requests-remaining':'8'}, 'QUOTA_EXHAUSTED'),
    ({'x-requests-remaining':'100','x-requests-last':'4'}, 'COST_CHANGED')])
def test_quota_fail_closed_at_next_checkpoint(setup, headers, expected):
    path, clock, game=setup
    run(setup, lambda *a, **k: HttpJsonResponse([event(game, clock)], 200, headers))
    clock.now += timedelta(hours=18)
    assert run(setup, lambda *a, **k: pytest.fail('quota exceeded'))['state']==expected


def test_concurrent_ticks_reserve_once(setup):
    calls=[]
    def fetch(*a, **k):
        calls.append(1)
        return response(setup[2], setup[1])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:run(setup, fetch), range(2)))
    assert len(calls)==1
    assert {r['state'] for r in results}=={'HEALTHY','IDLE'}


def test_crash_reservation_survives_restart(setup):
    def crash(*a, **k): raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt): run(setup, crash)
    path, clock, _=setup
    clock.now += timedelta(minutes=31)
    assert run(setup, lambda *a, **k:pytest.fail('replay'))['state']=='IDLE'
    report=capture.report(path, clock=clock)
    assert report['credits_reserved']==3
    assert report['alerts'][0]['state']=='INTERRUPTED_CAPTURE'


def test_sql_trigger_rejects_backdated_insert_after_kickoff(setup):
    path, clock, _=setup
    run(setup)
    with capture.connect(path, clock) as connection:
        row=list(connection.execute('SELECT * FROM captures LIMIT 1').fetchone())
    clock.now += timedelta(days=1)
    row[8]='another-book'
    with capture.connect(path, clock) as connection:
        with pytest.raises(sqlite3.IntegrityError, match='pregame write boundary'):
            connection.execute('INSERT INTO captures VALUES('+','.join('?'*15)+')', row)


def test_safety_existing_file_week3_production_and_manifest(setup, tmp_path):
    path, clock, game=setup
    before=path.read_bytes()
    with pytest.raises(FileExistsError): capture.create(path, season=2030, phase='regular', week=1, games=[game], clock=clock)
    assert path.read_bytes()==before
    with pytest.raises(ValueError, match='Week 3'):
        capture.create(tmp_path/'week3.db', season=2026, phase='preseason', week=3, games=[game], clock=clock)
    with pytest.raises(ValueError, match='predictions.db'):
        capture.safe_path(tmp_path/'predictions.db')
    with pytest.raises(ValueError, match='predictions.db'):
        capture.safe_path(capture.PRODUCTION_DATABASE)
    with capture.connect(path, clock) as connection:
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            connection.execute("UPDATE games SET kickoff='2099-01-01T00:00:00Z'")
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            connection.execute('UPDATE experiment SET credits_limit=99999')


def test_report_is_readonly(setup):
    path, clock, _=setup
    before=path.read_bytes()
    clock.now+=timedelta(days=2)
    capture.report(path, clock=clock)
    assert path.read_bytes()==before


def test_espn_discovery_suppresses_legacy_production_writes(monkeypatch):
    from backend.app.services import nfl_product_service as product
    from backend.app.services import nfl_experiment_service as experiment
    def schedule(*args):
        experiment.record_schedule_refresh(provider='espn', season=2030, season_type='regular',
                                           provider_week=1, refreshed_at='2030-01-01', state='HEALTHY', game_count=0)
        return ['canonical slate']
    monkeypatch.setattr(product, '_schedule', schedule)
    monkeypatch.setattr(experiment, 'get_db_connection', lambda:pytest.fail('production storage accessed'))
    assert capture.espn_slate(2030,'regular',1)==['canonical slate']


def test_cli_requires_explicit_paid_authorization(setup):
    with pytest.raises(SystemExit): capture.main(['tick','--db',str(setup[0])])


def test_late_checkpoint_response_not_mislabeled_success(setup):
    path, clock, game=setup
    def fetch(*a, **k):
        clock.now += timedelta(minutes=30)
        return response(game, clock)
    assert run(setup, fetch)['games'][game['game_id']]=='MISSED_CAPTURE'
    with capture.connect(path, clock) as connection:
        assert connection.execute('SELECT COUNT(*) FROM captures').fetchone()[0]==0


def test_unchanged_odds_at_new_checkpoint_are_new_evidence(setup):
    path, clock, game=setup
    run(setup)
    clock.now += timedelta(hours=18)
    run(setup)
    with capture.connect(path, clock) as connection:
        assert connection.execute('SELECT COUNT(*) FROM captures').fetchone()[0]==6
        assert connection.execute('SELECT COUNT(*) FROM quotes').fetchone()[0]==12
    assert capture.report(path, clock=clock)['credits_reserved']==6


def test_preserve_budget_for_uncovered_later_games(tmp_path):
    clock=Clock()
    game=dict(game_id='g1',home_team='BUF',away_team='MIA',kickoff_time=capture.iso(clock()+timedelta(days=1)))
    later=dict(game_id='g2',home_team='NYJ',away_team='NE',kickoff_time=capture.iso(clock()+timedelta(days=3)))
    path=tmp_path/'breadth.db'
    capture.create(path,season=2030,phase='regular',week=1,games=[game,later],credits=6,clock=clock)
    run((path,clock,game))
    clock.now+=timedelta(hours=18)
    assert run((path,clock,game),lambda *a,**k:pytest.fail('repeat spends future coverage'))['state']=='BREADTH_PRIORITY'
    assert capture.report(path,clock=clock)['credits_reserved']==3


def test_quota_reconciliation_preserves_budget_and_attempts(setup):
    path,clock,game=setup
    run(setup,lambda *a,**k:HttpJsonResponse([event(game,clock)],200,{}))
    capture.reconcile_quota(path,200)
    status=capture.report(path,clock=clock)
    assert status['quota_state']=='KNOWN' and status['credits_reserved']==3
    assert len(status['requests'])==1
    assert run(setup,lambda *a,**k:pytest.fail('replayed checkpoint'))['state']=='IDLE'


def test_retry_after_respected(setup):
    path,clock,game=setup
    def fetch(*a,**k):
        raise StructuredHttpError(status=429,classification='RATE_LIMITED',message='slow',url='https://example.test',
                                  headers={'x-requests-remaining':'100','retry-after':str(24*3600)})
    assert run(setup,fetch)['state']=='RATE_LIMITED'
    clock.now+=timedelta(hours=18)
    assert run(setup,lambda *a,**k:pytest.fail('retry-after violated'))['state']=='IDLE'


def test_wrong_file_is_read_only_rejected(tmp_path):
    path=tmp_path/'unrelated.db'
    with sqlite3.connect(path) as c: c.execute('CREATE TABLE unrelated(x)')
    before=path.read_bytes()
    with pytest.raises(ValueError,match='Not a SmartBets'):
        capture.report(path)
    assert path.read_bytes()==before


def test_empty_and_past_slates_rejected(tmp_path):
    clock=Clock()
    for games in [[],[dict(game_id='past',home_team='BUF',away_team='MIA',kickoff_time=capture.iso(clock()))]]:
        with pytest.raises(ValueError):
            capture.create(tmp_path/'bad.db',season=2030,phase='regular',week=1,games=games,clock=clock)
        assert not (tmp_path/'bad.db').exists()


def test_no_paid_call_before_due_window(setup):
    path,clock,_=setup
    clock.now-=timedelta(minutes=1)
    assert run(setup,lambda *a,**k:pytest.fail('too early'))['state']=='IDLE'
    assert capture.report(path,clock=clock)['credits_reserved']==0


def test_crashed_request_requires_quota_reconciliation_at_next_checkpoint(setup):
    def crash(*a,**k): raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt): run(setup,crash)
    setup[1].now+=timedelta(hours=18)
    assert run(setup,lambda *a,**k:pytest.fail('unreconciled crash'))['state']=='QUOTA_UNKNOWN'


def test_naive_source_time_rejected(setup):
    payload=response(setup[2],setup[1])
    for market in payload.payload[0]['bookmakers'][0]['markets']:
        market['last_update']='2030-09-01T18:00:00'
    assert run(setup,lambda *a,**k:payload)['games'][setup[2]['game_id']]=='MALFORMED_RESPONSE'


def test_naive_manifest_kickoff_rejected(tmp_path):
    game=dict(game_id='g',home_team='BUF',away_team='MIA',kickoff_time='2030-09-02T18:00:00')
    with pytest.raises(ValueError):
        capture.create(tmp_path/'naive.db',season=2030,phase='regular',week=1,games=[game],clock=Clock())


def test_cli_offline_create_report_and_auth_failure(tmp_path, capsys):
    game=dict(game_id='g',home_team='BUF',away_team='MIA',kickoff_time=capture.iso(capture.utcnow()+timedelta(days=1)))
    source=tmp_path/'games.json'
    source.write_text(json.dumps([game]),encoding='utf-8')
    path=tmp_path/'cli.db'
    assert capture.main(['create','--db',str(path),'--season','2030','--games-json',str(source)])==0
    assert json.loads(capsys.readouterr().out)['manifest_verified']
    assert capture.main(['report','--db',str(path)])==0
    assert json.loads(capsys.readouterr().out)['credits_reserved']==0
    assert capture.main(['tick','--db',str(path),'--allow-paid'])==2
    assert json.loads(capsys.readouterr().out)['state']=='AUTH_ERROR'


def test_workflow_leaves_production_database_byte_identical(setup):
    path=capture.PRODUCTION_DATABASE
    before=path.read_bytes() if path.exists() else None
    run(setup)
    capture.report(setup[0],clock=setup[1])
    assert (path.read_bytes() if path.exists() else None)==before


def test_watch_requires_key_before_waiting_for_future_checkpoint(setup, capsys):
    assert capture.main(['watch','--db',str(setup[0]),'--allow-paid'])==2
    assert json.loads(capsys.readouterr().out)['reason']=='MISSING_API_KEY'
