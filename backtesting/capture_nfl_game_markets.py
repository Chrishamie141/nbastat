"""Coverage-first, standalone NFL market experiments. Never opens predictions.db.

One durable attempt per game/checkpoint; one league-wide request for all due games.
No model fitting, prediction generation, automatic retries, or historical odds.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlencode
from unittest.mock import patch

from backtesting.game_matching import match_game, normalize_team, parse_dt
from backend.app.services.nfl_experiment_service import is_strictly_pregame
from database_safety import PRODUCTION_DATABASE, assert_sqlite_target
from nfl_providers import (HttpJsonResponse, StructuredHttpError, NFL_SPORT_KEY,
                           ODDS_API_BASE, _fetch_json_structured)

APPLICATION_ID = 1396851533
MARKETS = ('h2h', 'spreads', 'totals')
CHECKPOINTS = ((1440, 30), (360, 15), (60, 5))  # offset and late tolerance, minutes
COST = 3  # fixed US region, three featured markets; all games in one call
ENDPOINT = f'{ODDS_API_BASE}/sports/{NFL_SPORT_KEY}/odds'


def utcnow():
    return datetime.now(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def aware_dt(value):
    """Live evidence must state its timezone; never guess a local kickoff's zone."""
    try:
        original = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parse_dt(value) if original.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def safe_path(path):
    path = Path(path).resolve()
    production = PRODUCTION_DATABASE.resolve()
    if (path.name.lower() == 'predictions.db' or path == production or
            (path.exists() and production.exists() and path.samefile(production))):
        raise ValueError('The capture workflow cannot open predictions.db')
    assert_sqlite_target(path)
    return path


@contextmanager
def connect(path, clock=utcnow, readonly=False):
    path = safe_path(path)
    mode = 'ro' if readonly else 'rw'
    connection = sqlite3.connect(path.as_uri() + '?mode=' + mode, uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        if connection.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID:
            raise ValueError('Not a SmartBets market-capture database')
        connection.execute('PRAGMA foreign_keys=ON')
        connection.create_function('capture_now', 0, lambda: clock().timestamp())
        with connection:
            yield connection
    finally:
        connection.close()


SCHEMA = """
CREATE TABLE experiment(id INTEGER PRIMARY KEY CHECK(id=1), manifest TEXT NOT NULL,
 manifest_hash TEXT NOT NULL, credits_limit INTEGER NOT NULL, reserved INTEGER NOT NULL DEFAULT 0,
 remaining INTEGER, quota_state TEXT NOT NULL DEFAULT 'BOOTSTRAP', next_request REAL NOT NULL DEFAULT 0,
 last_status TEXT NOT NULL DEFAULT 'READY');
CREATE TABLE games(game_id TEXT PRIMARY KEY, home_team TEXT NOT NULL, away_team TEXT NOT NULL,
 kickoff TEXT NOT NULL, kickoff_epoch REAL NOT NULL);
CREATE TABLE checkpoints(game_id TEXT NOT NULL REFERENCES games(game_id), offset_minutes INTEGER NOT NULL,
 due REAL NOT NULL, deadline REAL NOT NULL, state TEXT NOT NULL DEFAULT 'PENDING',
 PRIMARY KEY(game_id,offset_minutes));
CREATE TABLE requests(id INTEGER PRIMARY KEY, requested_at TEXT NOT NULL, completed_at TEXT,
 state TEXT NOT NULL, credits_reserved INTEGER NOT NULL, remaining INTEGER, used INTEGER,
 last_cost INTEGER, response_hash TEXT, http_status INTEGER);
CREATE TABLE captures(game_id TEXT NOT NULL, offset_minutes INTEGER NOT NULL,
 request_id INTEGER NOT NULL REFERENCES requests(id), retrieved_at TEXT NOT NULL,
 stored_at TEXT NOT NULL, kickoff TEXT NOT NULL, cutoff_epoch REAL NOT NULL,
 event_id TEXT NOT NULL, bookmaker TEXT NOT NULL, market_type TEXT NOT NULL,
 source_time TEXT NOT NULL, source_epoch REAL NOT NULL, retrieved_epoch REAL NOT NULL,
 outcomes TEXT NOT NULL, provenance TEXT NOT NULL,
 PRIMARY KEY(game_id,offset_minutes,bookmaker,market_type),
 FOREIGN KEY(game_id,offset_minutes) REFERENCES checkpoints(game_id,offset_minutes));
CREATE TABLE quotes(game_id TEXT NOT NULL, offset_minutes INTEGER NOT NULL, bookmaker TEXT NOT NULL,
 market_type TEXT NOT NULL, side TEXT NOT NULL, line REAL, price REAL NOT NULL,
 PRIMARY KEY(game_id,offset_minutes,bookmaker,market_type,side),
 FOREIGN KEY(game_id,offset_minutes,bookmaker,market_type)
 REFERENCES captures(game_id,offset_minutes,bookmaker,market_type));
CREATE TRIGGER captures_pregame BEFORE INSERT ON captures BEGIN
 SELECT CASE WHEN capture_now() >= NEW.cutoff_epoch
 OR NEW.retrieved_epoch >= NEW.cutoff_epoch OR NEW.source_epoch >= NEW.cutoff_epoch
 OR NEW.source_epoch > NEW.retrieved_epoch
 OR NEW.cutoff_epoch > (SELECT kickoff_epoch FROM games WHERE game_id=NEW.game_id)
 OR capture_now() >= (SELECT deadline FROM checkpoints WHERE game_id=NEW.game_id AND offset_minutes=NEW.offset_minutes)
 OR NEW.retrieved_epoch >= (SELECT deadline FROM checkpoints WHERE game_id=NEW.game_id AND offset_minutes=NEW.offset_minutes)
 THEN RAISE(ABORT,'pregame write boundary') END;
END;
CREATE TRIGGER captures_no_update BEFORE UPDATE ON captures BEGIN SELECT RAISE(ABORT,'immutable capture'); END;
CREATE TRIGGER captures_no_delete BEFORE DELETE ON captures BEGIN SELECT RAISE(ABORT,'immutable capture'); END;
CREATE TRIGGER quotes_pregame BEFORE INSERT ON quotes BEGIN
 SELECT CASE WHEN capture_now() >= (SELECT cutoff_epoch FROM captures WHERE game_id=NEW.game_id
 AND offset_minutes=NEW.offset_minutes AND bookmaker=NEW.bookmaker AND market_type=NEW.market_type)
 OR capture_now() >= (SELECT deadline FROM checkpoints WHERE game_id=NEW.game_id AND offset_minutes=NEW.offset_minutes)
 THEN RAISE(ABORT,'pregame write boundary') END;
END;
CREATE TRIGGER quotes_no_update BEFORE UPDATE ON quotes BEGIN SELECT RAISE(ABORT,'immutable quote'); END;
CREATE TRIGGER quotes_no_delete BEFORE DELETE ON quotes BEGIN SELECT RAISE(ABORT,'immutable quote'); END;
CREATE TRIGGER games_no_update BEFORE UPDATE ON games BEGIN SELECT RAISE(ABORT,'immutable slate'); END;
CREATE TRIGGER games_no_delete BEFORE DELETE ON games BEGIN SELECT RAISE(ABORT,'immutable slate'); END;
CREATE TRIGGER manifest_no_update BEFORE UPDATE OF manifest,manifest_hash,credits_limit ON experiment
 BEGIN SELECT RAISE(ABORT,'immutable experiment configuration'); END;
CREATE TRIGGER manifest_no_delete BEFORE DELETE ON experiment BEGIN SELECT RAISE(ABORT,'immutable experiment'); END;
"""


def create(path, *, season, phase, week, games, credits=90, clock=utcnow):
    """Freeze a new slate and config; refuse existing stores, past games and Week 3."""
    if (season, phase, week) == (2026, 'preseason', 3):
        raise ValueError('Week 3 is frozen and excluded from this workflow')
    if phase not in {'preseason', 'regular'} or season < 2026 or week < 1:
        raise ValueError('Invalid experiment season/phase/week')
    if not games or credits < COST:
        raise ValueError('A nonempty slate and at least three authorized credits are required')
    now = clock()
    slate = []
    for game in games:
        kickoff = aware_dt(game.get('kickoff_time'))
        if not kickoff or kickoff <= now or game.get('status', 'scheduled') != 'scheduled':
            raise ValueError('Only future scheduled games may be frozen')
        if (int(game.get('season', season)) != season or game.get('season_type', phase) != phase
                or int(game.get('display_week', game.get('week', week))) != week):
            raise ValueError('Game does not belong to the requested experiment')
        home, away = normalize_team(game.get('home_team')), normalize_team(game.get('away_team'))
        if not game.get('game_id') or not home or not away or home == away:
            raise ValueError('Invalid game identity')
        slate.append(dict(game_id=str(game['game_id']), home_team=home, away_team=away,
                          kickoff_time=iso(kickoff), kickoff_epoch=kickoff.timestamp(),
                          schedule_source=str(game.get('provider') or 'operator_manifest'),
                          input_game_sha256=digest(game)))
    slate.sort(key=lambda game: game['game_id'])
    if len({game['game_id'] for game in slate}) != len(slate):
        raise ValueError('Duplicate canonical game identity')
    manifest = dict(schema_version=1, purpose='market_coverage_only', season=season, phase=phase,
                    week=week, created_at=iso(now), games=slate, checkpoints=CHECKPOINTS,
                    markets=MARKETS, region='us', credits_limit=credits, max_source_age_seconds=900,
                    frozen_model_reference='nfl_game_baseline_v3', model_tuning=False)
    path = safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation makes a typo/repeated init unable to overwrite any existing database.
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    connection = sqlite3.connect(path)
    try:
        with connection:
            connection.executescript(SCHEMA)
            connection.execute(f'PRAGMA application_id={APPLICATION_ID}')
            connection.execute('INSERT INTO experiment(id,manifest,manifest_hash,credits_limit) VALUES(1,?,?,?)',
                               (canonical(manifest), digest(manifest), credits))
            for game in slate:
                connection.execute('INSERT INTO games VALUES(?,?,?,?,?)',
                    (game['game_id'], game['home_team'], game['away_team'], game['kickoff_time'], game['kickoff_epoch']))
                for offset, tolerance in CHECKPOINTS:
                    due = game['kickoff_epoch'] - offset * 60
                    connection.execute('INSERT INTO checkpoints(game_id,offset_minutes,due,deadline) VALUES(?,?,?,?)',
                                       (game['game_id'], offset, due, due + tolerance * 60))
    finally:
        connection.close()
    return report(path, clock=clock)


def manifest(connection):
    row = connection.execute('SELECT * FROM experiment WHERE id=1').fetchone()
    value = json.loads(row['manifest'])
    if digest(value) != row['manifest_hash']:
        raise ValueError('Capture manifest integrity failure')
    return row, value


def header_number(headers, name):
    try:
        value = int(headers[name])
        return value if value >= 0 else None
    except (KeyError, ValueError, TypeError):
        return None


def failure_state(error):
    if error.headers.get('x-requests-remaining') == '0' or 'usage credits' in error.provider_message.lower():
        return 'QUOTA_EXHAUSTED'
    if error.status in {401, 403}:
        return 'AUTH_ERROR'
    if error.status == 429:
        return 'RATE_LIMITED'
    if error.classification == 'INVALID_PROVIDER_RESPONSE':
        return 'MALFORMED_RESPONSE'
    return 'NETWORK_FAILURE' if error.status is None else 'UPSTREAM_ERROR'


def valid_outcomes(market, game):
    """Require a usable paired market; retain all line/price/side evidence."""
    kind = market['key']
    rows = market.get('outcomes', [])
    if len(rows) != 2:
        return None
    result = []
    for row in rows:
        price, line = row.get('price'), row.get('point')
        if (isinstance(price, bool) or not isinstance(price, (float, int))
                or not math.isfinite(price) or abs(price) < 100):
            return None
        if kind != 'h2h' and (isinstance(line, bool) or not isinstance(line, (float, int)) or not math.isfinite(line)):
            return None
        side = str(row.get('name', ''))
        side = side.lower() if kind == 'totals' else normalize_team(side)
        result.append(dict(side=side, line=None if kind == 'h2h' else line, price=price))
    expected = {'over', 'under'} if kind == 'totals' else {game['home_team'], game['away_team']}
    if {row['side'] for row in result} != expected:
        return None
    if kind == 'totals' and (result[0]['line'] != result[1]['line'] or result[0]['line'] <= 0):
        return None
    if kind == 'spreads' and abs(result[0]['line'] + result[1]['line']) > 1e-6:
        return None
    return sorted(result, key=lambda row: row['side'])


def store_game(connection, checkpoint, events, request_id, retrieved, clock):
    game = dict(checkpoint)
    game['kickoff_time'] = game['kickoff']
    matched = [event for event in events if isinstance(event, dict)
               and match_game(event, [game], tolerance_minutes=180, league='nfl').matched]
    if len(matched) != 1:
        return 'NO_MARKET' if not matched else 'AMBIGUOUS_EVENT'
    event = matched[0]
    provider_kickoff = aware_dt(event.get('commence_time'))
    if not provider_kickoff or abs(provider_kickoff.timestamp() - game['kickoff_epoch']) > 60:
        return 'SCHEDULE_CHANGED'
    cutoff = min(provider_kickoff.timestamp(), game['kickoff_epoch'])
    if max(clock().timestamp(), retrieved.timestamp()) >= cutoff:
        return 'KICKOFF_BLOCKED'
    if max(clock().timestamp(), retrieved.timestamp()) >= game['deadline']:
        return 'MISSED_CAPTURE'
    covered = set()
    invalid = stale = False
    for book in event.get('bookmakers', []):
        for market in book.get('markets', []):
            kind = market.get('key')
            if kind not in MARKETS:
                continue
            source = aware_dt(market.get('last_update') or book.get('last_update'))
            if not source or not is_strictly_pregame(market_timestamp=iso(source),
                    retrieved_at=iso(retrieved), kickoff_timestamp=iso(datetime.fromtimestamp(cutoff, timezone.utc))):
                invalid = True
                continue
            age = (retrieved - source).total_seconds()
            if age < 0 or age > 900:
                stale = True
                continue
            outcomes = valid_outcomes(market, game)
            if not outcomes or not book.get('key') or not event.get('id'):
                invalid = True
                continue
            if clock().timestamp() >= cutoff:
                return 'KICKOFF_BLOCKED'
            provenance = dict(provider='the-odds-api', endpoint=ENDPOINT, region='us',
                              event_id=event['id'], bookmaker_title=book.get('title'),
                              match_strategy='datetime_home_away_league', manifest_kickoff=game['kickoff'],
                              provider_kickoff=event['commence_time'], market_payload_sha256=digest(market))
            connection.execute('''INSERT INTO captures VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(game_id,offset_minutes,bookmaker,market_type) DO NOTHING''',
                (game['game_id'], game['offset_minutes'], request_id, iso(retrieved), iso(clock()),
                 iso(datetime.fromtimestamp(cutoff, timezone.utc)), cutoff, event['id'], book['key'], kind,
                 iso(source), source.timestamp(), retrieved.timestamp(), canonical(outcomes), canonical(provenance)))
            for outcome in outcomes:
                connection.execute('''INSERT INTO quotes VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(game_id,offset_minutes,bookmaker,market_type,side) DO NOTHING''',
                    (game['game_id'], game['offset_minutes'], book['key'], kind,
                     outcome['side'], outcome['line'], outcome['price']))
            covered.add(kind)
    if len(covered) == 3:
        return 'COMPLETE'
    if covered:
        return 'PARTIAL'
    return 'STALE_DATA' if stale else 'MALFORMED_RESPONSE' if invalid else 'NO_MARKET'


def tick(path, *, api_key=None, fetcher=None, clock=utcnow):
    """Run one bounded checkpoint batch. All reservations survive process crashes."""
    fetcher = fetcher or _fetch_json_structured
    key = api_key if api_key is not None else os.getenv('THE_ODDS_API_KEY') or os.getenv('ODDS_API_KEY')
    now = clock()
    with connect(path, clock) as connection:
        connection.execute('BEGIN IMMEDIATE')
        config, _ = manifest(connection)
        connection.execute("UPDATE checkpoints SET state='MISSED_CAPTURE' WHERE state='PENDING' AND deadline<=?", (now.timestamp(),))
        due = connection.execute('''SELECT c.*,g.home_team,g.away_team,g.kickoff,g.kickoff_epoch
            FROM checkpoints c JOIN games g USING(game_id) WHERE c.state='PENDING'
            AND c.due<=? AND c.deadline>? AND g.kickoff_epoch>?
            ORDER BY c.due,c.game_id''', (now.timestamp(), now.timestamp(), now.timestamp())).fetchall()
        if not due or config['next_request'] > now.timestamp():
            return {'state': 'IDLE', 'due_games': len(due)}
        covered_games = {row[0] for row in connection.execute('SELECT DISTINCT game_id FROM captures')}
        future_first = connection.execute('''SELECT game_id,MIN(due) AS first_due FROM checkpoints
            WHERE state='PENDING' AND due>? AND game_id NOT IN (SELECT DISTINCT game_id FROM captures)
            GROUP BY game_id''', (now.timestamp(),)).fetchall()
        breadth_reserve = COST * len({row['first_due'] for row in future_first})
        available = config['credits_limit'] - config['reserved']
        if config['remaining'] is not None:
            available = min(available, max(0, config['remaining'] - 6))
        if (breadth_reserve > 0 and all(row['game_id'] in covered_games for row in due)
                and available - COST < breadth_reserve):
            for row in due:
                connection.execute("UPDATE checkpoints SET state='BREADTH_PRIORITY' WHERE game_id=? AND offset_minutes=?",
                                   (row['game_id'], row['offset_minutes']))
            return {'state': 'BREADTH_PRIORITY', 'network_contacted': False, 'reserved_for_uncovered_games': breadth_reserve}
        stop = None
        if not key:
            stop = 'AUTH_ERROR'
        elif config['reserved'] + COST > config['credits_limit']:
            stop = 'BUDGET_EXHAUSTED'
        elif config['quota_state'] in {'AUTH_ERROR', 'QUOTA_EXHAUSTED', 'QUOTA_UNKNOWN', 'COST_CHANGED'}:
            stop = config['quota_state']
        elif config['remaining'] is not None and config['remaining'] < COST + 6:
            stop = 'QUOTA_EXHAUSTED'
        if stop:
            connection.execute('UPDATE experiment SET last_status=? WHERE id=1', (stop,))
            return {'state': stop, 'due_games': len(due), 'network_contacted': False}
        request_id = connection.execute('INSERT INTO requests(requested_at,state,credits_reserved) VALUES(?,?,?)',
                                       (iso(now), 'IN_FLIGHT', COST)).lastrowid
        connection.execute("UPDATE experiment SET reserved=reserved+?, next_request=?,quota_state='QUOTA_UNKNOWN' WHERE id=1",
                           (COST, now.timestamp() + 300))
        for row in due:
            connection.execute("UPDATE checkpoints SET state='IN_FLIGHT' WHERE game_id=? AND offset_minutes=?",
                               (row['game_id'], row['offset_minutes']))
    # Exactly one league-wide call; no cache, dashboard route, or per-game paid loop.
    params = dict(apiKey=key, regions='us', markets=','.join(MARKETS), oddsFormat='american')
    state, response = 'HEALTHY', None
    try:
        response = fetcher(ENDPOINT + '?' + urlencode(params), timeout=20)
        if not isinstance(response, HttpJsonResponse) or not isinstance(response.payload, list):
            state = 'MALFORMED_RESPONSE'
        else:
            canonical(response.payload)  # reject NaN/Infinity before persisting provenance
    except StructuredHttpError as error:
        state = failure_state(error)
        response = HttpJsonResponse([], error.status, error.headers)
    except (OSError, TimeoutError):
        state = 'NETWORK_FAILURE'
    except (ValueError, TypeError):
        state = 'MALFORMED_RESPONSE'
    retrieved = clock()  # after the complete HTTP body, not the request start
    headers = response.headers if isinstance(response, HttpJsonResponse) else {}
    remaining = header_number(headers, 'x-requests-remaining')
    used = header_number(headers, 'x-requests-used')
    cost = header_number(headers, 'x-requests-last')
    with connect(path, clock) as connection:
        connection.execute('BEGIN IMMEDIATE')
        quota = ('COST_CHANGED' if cost is not None and cost > COST else
                 state if state in {'AUTH_ERROR', 'QUOTA_EXHAUSTED'} else
                 'QUOTA_UNKNOWN' if remaining is None else 'KNOWN')
        retry_after = header_number(headers, 'retry-after') or 0
        connection.execute('UPDATE experiment SET remaining=?,quota_state=?,last_status=?,next_request=MAX(next_request,?) WHERE id=1',
                           (remaining, quota, state, retrieved.timestamp() + retry_after))
        connection.execute('''UPDATE requests SET completed_at=?,state=?,remaining=?,used=?,last_cost=?,
            response_hash=?,http_status=? WHERE id=?''',
            (iso(retrieved), state, remaining, used, cost,
             digest(response.payload) if state == 'HEALTHY' else None,
             response.status if isinstance(response, HttpJsonResponse) else None, request_id))
        states = {}
        for row in due:
            result = state
            if state == 'HEALTHY':
                # Per-game savepoint: a malformed bookmaker or kickoff race cannot lose
                # the entire batch, nor leave partially written late evidence behind.
                connection.execute('SAVEPOINT game_capture')
                try:
                    result = store_game(connection, row, response.payload, request_id, retrieved, clock)
                except (ValueError, TypeError, AttributeError, KeyError, sqlite3.IntegrityError) as error:
                    connection.execute('ROLLBACK TO game_capture')
                    result = 'KICKOFF_BLOCKED' if 'pregame write boundary' in str(error) else 'MALFORMED_RESPONSE'
                connection.execute('RELEASE game_capture')
            connection.execute('UPDATE checkpoints SET state=? WHERE game_id=? AND offset_minutes=?',
                               (result, row['game_id'], row['offset_minutes']))
            states[row['game_id']] = result
    return dict(state=state, request_id=request_id, reserved_credits=COST, games=states,
                quota_state=quota, remaining=remaining, network_contacted=True)


def report(path, *, clock=utcnow):
    """Read-only checkpoint coverage and actionable local alerts (never regrades)."""
    with connect(path, clock, readonly=True) as connection:
        config, frozen = manifest(connection)
        rows = []
        for row in connection.execute('SELECT c.*,g.kickoff FROM checkpoints c JOIN games g USING(game_id) ORDER BY g.kickoff,c.game_id,c.due'):
            markets = [item[0] for item in connection.execute('SELECT DISTINCT market_type FROM captures WHERE game_id=? AND offset_minutes=? ORDER BY market_type', (row['game_id'], row['offset_minutes']))]
            state = row['state']
            if state == 'PENDING' and clock().timestamp() >= row['deadline']:
                state = 'MISSED_CAPTURE'
            if state == 'IN_FLIGHT' and clock().timestamp() > row['deadline']:
                state = 'INTERRUPTED_CAPTURE'
            rows.append(dict(game_id=row['game_id'], kickoff=row['kickoff'], checkpoint_minutes=row['offset_minutes'],
                             state=state, markets=markets, coverage=f'{len(markets)}/3'))
        requests = [dict(row) for row in connection.execute('SELECT * FROM requests ORDER BY id')]
    alerts = [row for row in rows if row['state'] not in {'PENDING', 'COMPLETE', 'IN_FLIGHT'}]
    quota_state = config['quota_state']
    if config['reserved'] + COST > config['credits_limit']:
        quota_state = 'BUDGET_EXHAUSTED'
    elif config['remaining'] is not None and config['remaining'] < COST + 6:
        quota_state = 'QUOTA_EXHAUSTED'
    return dict(experiment=frozen, manifest_hash=config['manifest_hash'], manifest_verified=True,
                checkpoints=rows, alerts=alerts, requests=requests,
                complete_checkpoints=sum(row['state'] == 'COMPLETE' for row in rows),
                total_checkpoints=len(rows), games_with_any_market=len({row['game_id'] for row in rows if row['markets']}),
                credits_reserved=config['reserved'], credits_limit=config['credits_limit'],
                account_remaining=config['remaining'], quota_state=quota_state, last_status=config['last_status'])


def reconcile_quota(path, remaining):
    """Explicit operator reconciliation, never refund/replay reserved attempts."""
    if remaining < 0:
        raise ValueError('Remaining credits must be nonnegative')
    with connect(path) as connection:
        connection.execute('BEGIN IMMEDIATE')
        manifest(connection)
        connection.execute("UPDATE experiment SET remaining=?,quota_state='KNOWN',last_status='QUOTA_RECONCILED' WHERE id=1", (remaining,))
    return report(path)


def espn_slate(season, phase, week):
    """Reuse canonical mapping without its legacy production refresh-log writes."""
    from backend.app.services.nfl_product_service import _schedule
    with patch('backend.app.services.nfl_experiment_service.record_schedule_refresh'):
        return _schedule(season, week, phase)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['create', 'tick', 'report', 'watch', 'reconcile-quota'])
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--season', type=int, default=2026)
    parser.add_argument('--phase', choices=['preseason', 'regular'], default='regular')
    parser.add_argument('--week', type=int, default=1)
    parser.add_argument('--credits', type=int, default=90)
    parser.add_argument('--games-json', type=Path)
    parser.add_argument('--from-espn', action='store_true')
    parser.add_argument('--remaining', type=int, help='Externally verified account credits for explicit reconciliation')
    parser.add_argument('--allow-paid', action='store_true', help='Authorize paid calls within the frozen credit ceiling')
    args = parser.parse_args(argv)
    if args.action == 'create':
        # Validate destination/scope BEFORE any network request.
        path = safe_path(args.db)
        if path.exists() or (args.season, args.phase, args.week) == (2026, 'preseason', 3):
            parser.error('Existing destination or frozen Week 3 scope')
        if bool(args.games_json) == bool(args.from_espn):
            parser.error('Choose exactly one of --games-json or --from-espn')
        games = json.loads(args.games_json.read_text(encoding='utf-8')) if args.games_json else espn_slate(args.season, args.phase, args.week)
        result = create(path, season=args.season, phase=args.phase, week=args.week, games=games, credits=args.credits)
    elif args.action == 'report':
        result = report(args.db)
    elif args.action == 'reconcile-quota':
        if args.remaining is None:
            parser.error('reconcile-quota requires --remaining from the provider dashboard')
        result = reconcile_quota(args.db, args.remaining)
    else:
        if not args.allow_paid:
            parser.error('tick/watch requires --allow-paid; report is always offline')
        if args.action == 'watch' and not (os.getenv('THE_ODDS_API_KEY') or os.getenv('ODDS_API_KEY')):
            print(canonical({'state': 'AUTH_ERROR', 'reason': 'MISSING_API_KEY', 'network_contacted': False}))
            return 2
        if args.action == 'tick':
            result = tick(args.db)
        else:
            previous = None
            while True:
                outcome = tick(args.db)
                status = report(args.db)
                signature = digest({'checkpoints': status['checkpoints'], 'quota': status['quota_state'], 'outcome': outcome['state']})
                if signature != previous:
                    print(canonical(dict(outcome=outcome, coverage=status)), flush=True)
                    previous = signature
                if all(row['state'] not in {'PENDING', 'IN_FLIGHT'} for row in status['checkpoints']):
                    return 0
                if (outcome['state'] in {'AUTH_ERROR', 'QUOTA_EXHAUSTED', 'QUOTA_UNKNOWN', 'BUDGET_EXHAUSTED', 'COST_CHANGED'}
                        or status['quota_state'] in {'AUTH_ERROR', 'QUOTA_EXHAUSTED', 'QUOTA_UNKNOWN', 'BUDGET_EXHAUSTED', 'COST_CHANGED'}):
                    return 2
                time.sleep(60)  # local scheduling only; API requests are checkpoint-gated
    print(json.dumps(result, indent=2))
    if args.action == 'tick' and (result['state'] in {
            'AUTH_ERROR', 'QUOTA_EXHAUSTED', 'QUOTA_UNKNOWN', 'BUDGET_EXHAUSTED',
            'COST_CHANGED', 'RATE_LIMITED', 'NETWORK_FAILURE', 'UPSTREAM_ERROR', 'MALFORMED_RESPONSE'}):
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
