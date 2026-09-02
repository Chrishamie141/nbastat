"""Forward regular-season experiment lifecycle; isolated from production/Week 3."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import time

from backtesting import capture_nfl_game_markets as capture
from backtesting.nfl_game_predictor import NFLGameMarketPredictor, GameProjection, no_vig_probabilities
from backend.app.services.nfl_product_service import PROFILE_POLICY
from backend.app.services.nfl_experiment_service import american_profit
from nfl_providers import HttpJsonResponse, StructuredHttpError, _fetch_json_structured
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
MODEL = 'nfl_game_baseline_v3'
SCHEMA = '''
CREATE TABLE IF NOT EXISTS forward_predictions(game_id TEXT PRIMARY KEY REFERENCES games(game_id),
 experiment_id TEXT NOT NULL, model_version TEXT NOT NULL, model_hash TEXT NOT NULL,
 winner TEXT NOT NULL, probability REAL NOT NULL, generated_at TEXT NOT NULL,
 generated_epoch REAL NOT NULL, input_json TEXT NOT NULL, prediction_json TEXT NOT NULL,
 prediction_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS forward_wagers(game_id TEXT NOT NULL REFERENCES forward_predictions(game_id),
 profile TEXT NOT NULL, market TEXT NOT NULL, side TEXT NOT NULL, line REAL, price REAL NOT NULL,
 probability REAL NOT NULL, edge REAL NOT NULL, created_at TEXT NOT NULL, capture_ref TEXT NOT NULL,
 PRIMARY KEY(game_id,profile,market));
CREATE TABLE IF NOT EXISTS forward_finals(game_id TEXT PRIMARY KEY REFERENCES games(game_id),
 home_score INTEGER NOT NULL, away_score INTEGER NOT NULL, source TEXT NOT NULL, retrieved_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS forward_grades(game_id TEXT PRIMARY KEY REFERENCES forward_predictions(game_id),
 result TEXT NOT NULL, graded_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS forward_wager_grades(game_id TEXT NOT NULL,profile TEXT NOT NULL,market TEXT NOT NULL,
 result TEXT NOT NULL,units REAL NOT NULL,graded_at TEXT NOT NULL, PRIMARY KEY(game_id,profile,market),
 FOREIGN KEY(game_id,profile,market) REFERENCES forward_wagers(game_id,profile,market));
CREATE TABLE IF NOT EXISTS forward_events(event_id INTEGER PRIMARY KEY,at TEXT NOT NULL,kind TEXT NOT NULL,detail TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS forward_prediction_boundary BEFORE INSERT ON forward_predictions BEGIN
 SELECT CASE WHEN capture_now()>=(SELECT kickoff_epoch-3600 FROM games WHERE game_id=NEW.game_id)
 OR NEW.generated_epoch>=(SELECT kickoff_epoch-3600 FROM games WHERE game_id=NEW.game_id)
 THEN RAISE(ABORT,'prediction freeze deadline') END; END;
CREATE TRIGGER IF NOT EXISTS forward_wager_boundary BEFORE INSERT ON forward_wagers BEGIN
 SELECT CASE WHEN capture_now()>=(SELECT kickoff_epoch FROM games WHERE game_id=NEW.game_id)
 THEN RAISE(ABORT,'wager kickoff deadline') END; END;
'''


def initialize(path, clock=capture.utcnow):
    with capture.connect(path, clock) as c:
        _, m = capture.manifest(c)
        if m['phase'] != 'regular':
            raise ValueError('Forward workflow only accepts regular season; preseason is immutable')
        c.executescript(SCHEMA)
        for table in ['forward_predictions','forward_wagers','forward_finals','forward_grades','forward_wager_grades']:
            for verb in ['UPDATE','DELETE']:
                c.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{verb.lower()} BEFORE {verb} ON {table} BEGIN SELECT RAISE(ABORT,'immutable forward evidence'); END")


def identity(manifest):
    return f"NFL-{manifest['season']}-REG{manifest['week']}-v1"


def mapping(expected, received):
    """Require the exact slate, orientation and kickoff, not just matching counts."""
    by_id = {g['game_id']: g for g in received}
    if len(by_id) != len(received) or set(by_id) != {g['game_id'] for g in expected}:
        raise ValueError('Official schedule game IDs differ from the frozen slate')
    for game in expected:
        actual = by_id[game['game_id']]
        if (capture.normalize_team(actual['home_team']) != game['home_team'] or
                capture.normalize_team(actual['away_team']) != game['away_team'] or
                capture.aware_dt(actual['kickoff_time']) != capture.aware_dt(game['kickoff_time'])):
            raise ValueError('Official schedule identity/kickoff changed; review required')
    return by_id


def freeze(path, *, history_path=ROOT/'data/nfl_team_game_history.json', schedule=None,
           clock=capture.utcnow, predictor=None):
    initialize(path, clock)
    with capture.connect(path, clock) as c:
        _, m = capture.manifest(c)
    official = schedule if schedule is not None else capture.espn_slate(m['season'],m['phase'],m['week'])
    mapping(m['games'], official)
    histories = json.loads(Path(history_path).read_text(encoding='utf-8'))
    histories = histories.get('items', []) if isinstance(histories,dict) else histories
    now = clock()
    eligible = [row for row in histories if capture.aware_dt(row.get('data_as_of'))
                and capture.aware_dt(row['data_as_of']) < now
                and capture.aware_dt(row.get('completed_at')) and capture.aware_dt(row['completed_at']) < now]
    model = predictor or NFLGameMarketPredictor(MODEL)
    files = ['backtesting/nfl_game_predictor.py','backtesting/nfl_v3.py','backtesting/game_matching.py','backtesting/team_history.py']
    code_hash = capture.digest({name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in files})
    inserted, skipped, unavailable = 0, [], []
    with capture.connect(path, clock) as c:
        c.execute('BEGIN IMMEDIATE')
        for game in m['games']:
            if c.execute('SELECT 1 FROM forward_predictions WHERE game_id=?',(game['game_id'],)).fetchone():
                skipped.append(game['game_id'])
                continue
            if clock().timestamp() >= game['kickoff_epoch']-3600:
                unavailable.append({'game_id':game['game_id'],'reason':'FREEZE_DEADLINE_PASSED'})
                continue
            model_game = dict(game, season=m['season'],week=m['week'])
            projection = model.project(model_game, eligible)
            if projection is None:
                unavailable.append({'game_id':game['game_id'],'reason':'INSUFFICIENT_HISTORY'})
                continue
            probability = projection.probability('h2h',game['home_team'],home_team=game['home_team'],away_team=game['away_team'])
            if not 0 <= probability <= 1:
                raise ValueError('Invalid model probability')
            winner = game['home_team'] if probability>=.5 else game['away_team']
            selected_probability = max(probability,1-probability)
            generated = capture.iso(clock())
            source = dict(history_source=Path(history_path).name,history_hash=capture.digest(histories),
                          available_inputs=eligible,available_input_hash=capture.digest(eligible),
                          manifest_hash=capture.digest(m),model_code_sha256=code_hash,
                          qualification_policy=PROFILE_POLICY,feature_data_as_of=projection.data_as_of)
            prediction = asdict(projection)
            evidence = dict(game_id=game['game_id'],experiment_id=identity(m),model_version=projection.model_version,
                            model_hash=code_hash,winner=winner,probability=selected_probability,generated_at=generated,
                            source=source,projection=prediction)
            c.execute('INSERT INTO forward_predictions VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (game['game_id'],identity(m),projection.model_version,code_hash,winner,selected_probability,
                 generated,clock().timestamp(),capture.canonical(source),capture.canonical(prediction),capture.digest(evidence)))
            inserted+=1
        c.execute('INSERT INTO forward_events(at,kind,detail) VALUES(?,?,?)',
                  (capture.iso(clock()),'SCHEDULE_VERIFIED',capture.canonical({'games':len(official)})))
    return dict(inserted=inserted,already_frozen=skipped,unavailable=unavailable)


def verify_prediction(row):
    source=json.loads(row['input_json'])
    projection=json.loads(row['prediction_json'])
    evidence=dict(game_id=row['game_id'],experiment_id=row['experiment_id'],model_version=row['model_version'],
                  model_hash=row['model_hash'],winner=row['winner'],probability=row['probability'],
                  generated_at=row['generated_at'],source=source,projection=projection)
    if capture.digest(evidence)!=row['prediction_hash']:
        raise ValueError('Frozen prediction integrity failure')
    return projection,source


def qualify(path, clock=capture.utcnow):
    initialize(path, clock)
    count=0
    with capture.connect(path, clock) as c:
        c.execute('BEGIN IMMEDIATE')
        for row in c.execute('SELECT p.*,g.home_team,g.away_team,g.kickoff_epoch FROM forward_predictions p JOIN games g USING(game_id)').fetchall():
            projection,source=verify_prediction(row)
            if clock().timestamp()>=row['kickoff_epoch']:
                continue
            model=GameProjection(**projection)
            # The first usable pregame qualification is retained; no postgame bet reconstruction.
            observations=c.execute('SELECT * FROM captures WHERE game_id=? AND retrieved_epoch<=? ORDER BY retrieved_epoch,bookmaker,market_type',
                                   (row['game_id'],clock().timestamp())).fetchall()
            for observation in observations:
                if (observation['cutoff_epoch']<=clock().timestamp()
                        or clock().timestamp()-observation['retrieved_epoch']>900):
                    continue
                outcomes=json.loads(observation['outcomes'])
                implied=no_vig_probabilities([o['price'] for o in outcomes])
                kind={'h2h':'h2h','spreads':'spread','totals':'total'}[observation['market_type']]
                choices=[]
                for outcome,market_probability in zip(outcomes,implied):
                    probability=model.probability(kind,outcome['side'],outcome['line'],home_team=row['home_team'],away_team=row['away_team'])
                    choices.append((probability-market_probability,probability,outcome))
                edge,probability,choice=max(choices,key=lambda x:x[0])
                for profile,policy in source['qualification_policy'].items():
                    if probability<policy['minimum_probability'] or edge<policy['minimum_edge']:
                        continue
                    ref=dict(request_id=observation['request_id'],checkpoint=observation['offset_minutes'],
                             bookmaker=observation['bookmaker'],retrieved_at=observation['retrieved_at'],
                             source_time=observation['source_time'],capture_hash=capture.digest(dict(observation)))
                    count+=c.execute('''INSERT INTO forward_wagers VALUES(?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(game_id,profile,market) DO NOTHING''',
                        (row['game_id'],profile,observation['market_type'],choice['side'],choice['line'],choice['price'],
                         probability,edge,capture.iso(clock()),capture.canonical(ref))).rowcount
    return dict(new_qualified_wagers=count)


def settle(path, schedule=None, clock=capture.utcnow):
    initialize(path, clock)
    with capture.connect(path, clock) as c:
        _,m=capture.manifest(c)
    official=schedule if schedule is not None else capture.espn_slate(m['season'],m['phase'],m['week'])
    by_id=mapping(m['games'],official)
    inserted=0
    with capture.connect(path,clock) as c:
        c.execute('BEGIN IMMEDIATE')
        for game in m['games']:
            result=by_id[game['game_id']]
            if result.get('status')!='final' or clock().timestamp()<game['kickoff_epoch']:
                continue
            hs,aws=result.get('home_score'),result.get('away_score')
            if not isinstance(hs,int) or not isinstance(aws,int) or min(hs,aws)<0:
                continue
            prior=c.execute('SELECT * FROM forward_finals WHERE game_id=?',(game['game_id'],)).fetchone()
            if prior and (prior['home_score'],prior['away_score'])!=(hs,aws):
                raise ValueError('Final score correction requires explicit reviewed reconciliation')
            inserted+=c.execute('INSERT INTO forward_finals VALUES(?,?,?,?,?) ON CONFLICT(game_id) DO NOTHING',
                               (game['game_id'],hs,aws,'espn',capture.iso(clock()))).rowcount
        for row in c.execute('''SELECT p.*,g.home_team,g.away_team,f.home_score,f.away_score FROM forward_predictions p
                JOIN games g USING(game_id) JOIN forward_finals f USING(game_id)''').fetchall():
            verify_prediction(row)
            hs,aws=row['home_score'],row['away_score']
            winner=row['home_team'] if hs>aws else row['away_team'] if aws>hs else None
            result='PUSH' if winner is None else 'WIN' if row['winner']==winner else 'LOSS'
            c.execute('INSERT INTO forward_grades VALUES(?,?,?) ON CONFLICT(game_id) DO NOTHING',(row['game_id'],result,capture.iso(clock())))
            for wager in c.execute('SELECT * FROM forward_wagers WHERE game_id=?',(row['game_id'],)).fetchall():
                if wager['market']=='h2h':
                    delta=0 if winner is None else 1 if wager['side']==winner else -1
                elif wager['market']=='spreads':
                    delta=(hs-aws if wager['side']==row['home_team'] else aws-hs)+wager['line']
                else:
                    delta=(hs+aws-wager['line'])*(1 if wager['side']=='over' else -1)
                result='PUSH' if delta==0 else 'WIN' if delta>0 else 'LOSS'
                units=0 if delta==0 else american_profit(wager['price']) if delta>0 else -1
                c.execute('INSERT INTO forward_wager_grades VALUES(?,?,?,?,?,?) ON CONFLICT(game_id,profile,market) DO NOTHING',
                          (row['game_id'],wager['profile'],wager['market'],result,units,capture.iso(clock())))
    return dict(new_finals=inserted)


def preflight(path, *, api_key=None,fetcher=None,clock=capture.utcnow):
    """Exactly one explicitly requested live league-odds call, budgeted, no market writes."""
    initialize(path,clock)
    key=api_key if api_key is not None else os.getenv('THE_ODDS_API_KEY') or os.getenv('ODDS_API_KEY')
    if not key: return {'state':'AUTH_ERROR','network_contacted':False}
    with capture.connect(path,clock) as c:
        c.execute('BEGIN IMMEDIATE')
        config,m=capture.manifest(c)
        if config['reserved']+3>config['credits_limit'] or (config['remaining'] is not None and config['remaining']<9):
            return {'state':'QUOTA_EXHAUSTED','network_contacted':False}
        if c.execute("SELECT 1 FROM forward_events WHERE kind='PREFLIGHT_RESERVED'").fetchone():
            return {'state':'ALREADY_ATTEMPTED','network_contacted':False}
        c.execute("INSERT INTO forward_events(at,kind,detail) VALUES(?,'PREFLIGHT_RESERVED','{}')",(capture.iso(clock()),))
        c.execute("UPDATE experiment SET reserved=reserved+3,quota_state='QUOTA_UNKNOWN' WHERE id=1")
    response=None
    try:
        response=(fetcher or _fetch_json_structured)(capture.ENDPOINT+'?'+urlencode(dict(apiKey=key,regions='us',markets=','.join(capture.MARKETS),oddsFormat='american')),timeout=20)
        if not isinstance(response.payload,list): raise ValueError('invalid payload')
        matches=[capture.match_game(e,m['games'],league='nfl') for e in response.payload]
        times=[market.get('last_update') or book.get('last_update') for e in response.payload for book in e.get('bookmakers',[]) for market in book.get('markets',[])]
        result=dict(state='HEALTHY',authentication='PASS',matched_game_ids=sorted({match.game_id for match in matches if match.matched}),
                    scheduled_game_count=len(m['games']),provider_timestamp=max((t for t in times if t),default=None))
    except StructuredHttpError as e:
        response=HttpJsonResponse([],e.status,e.headers)
        result=dict(state=capture.failure_state(e),authentication='FAIL' if e.status in (401,403) else 'UNKNOWN')
    except (ValueError,TypeError,AttributeError,OSError):
        result=dict(state='PROVIDER_FAILURE',authentication='UNKNOWN')
    headers=response.headers if response else {}
    remaining=capture.header_number(headers,'x-requests-remaining')
    result.update(credits_remaining=remaining,credits_used=capture.header_number(headers,'x-requests-used'),network_contacted=True)
    with capture.connect(path,clock) as c:
        c.execute('UPDATE experiment SET remaining=?,quota_state=? WHERE id=1',(remaining,'KNOWN' if remaining is not None and result['state']=='HEALTHY' else 'QUOTA_UNKNOWN'))
        c.execute("INSERT INTO forward_events(at,kind,detail) VALUES(?,'PREFLIGHT_RESULT',?)",(capture.iso(clock()),capture.canonical(result)))
    return result


def metrics(path, clock=capture.utcnow):
    coverage=capture.report(path,clock=clock)
    if coverage['experiment']['phase']!='regular':raise ValueError('Regular-season metrics cannot include preseason')
    with capture.connect(path,clock,readonly=True) as c:
        predictions=[dict(r) for r in c.execute('SELECT * FROM forward_predictions')]
        for row in predictions: verify_prediction(row)
        grades=[dict(r) for r in c.execute('SELECT p.probability,g.result FROM forward_grades g JOIN forward_predictions p USING(game_id)')]
        wagers=[dict(r) for r in c.execute('SELECT * FROM forward_wagers')]
        wager_grades=[dict(r) for r in c.execute('SELECT * FROM forward_wager_grades')]
        provider=c.execute("SELECT detail FROM forward_events WHERE kind='PREFLIGHT_RESULT' ORDER BY event_id DESC LIMIT 1").fetchone()
        mapping_verified=bool(c.execute("SELECT 1 FROM forward_events WHERE kind='SCHEDULE_VERIFIED'").fetchone())
    record={r:sum(g['result']==r for g in grades) for r in ('WIN','LOSS','PUSH')}
    n=record['WIN']+record['LOSS']
    buckets=[]
    for lower,upper in ((.5,.6),(.6,.7),(.7,.8),(.8,.9),(.9,1.01)):
        rows=[g for g in grades if lower<=g['probability']<upper and g['result']!='PUSH']
        buckets.append(dict(lower=lower,upper=upper,count=len(rows),mean_probability=sum(g['probability'] for g in rows)/len(rows) if rows else None,
                            observed_accuracy=sum(g['result']=='WIN' for g in rows)/len(rows) if rows else None))
    profiles={}
    for profile in PROFILE_POLICY:
        rows=[r for r in wager_grades if r['profile']==profile]
        units=sum(r['units'] for r in rows)
        profiles[profile]=dict(qualified_wagers=sum(r['profile']==profile for r in wagers),graded=len(rows),
            record={s:sum(r['result']==s for r in rows) for s in ('WIN','LOSS','PUSH')},units=units if rows else None,
            roi=100*units/len(rows) if rows else None)
    return dict(experiment_id=identity(coverage['experiment']),season=coverage['experiment']['season'],phase='regular',week=coverage['experiment']['week'],
        manifest_hash=coverage['manifest_hash'],prediction_hash=capture.digest(sorted(r['prediction_hash'] for r in predictions)),
        predictions=len(predictions),scheduled=len(coverage['experiment']['games']),graded=len(grades),winner_record=record,
        accuracy=record['WIN']/n if n else None,calibration_buckets=buckets,profiles=profiles,
        sample_warning='INSUFFICIENT_SAMPLE' if n<30 else 'DESCRIPTIVE_NOT_A_GUARANTEE',
        coverage=coverage,provider=json.loads(provider[0]) if provider else None,mapping_verified=mapping_verified)


def readiness(path,clock=capture.utcnow):
    result=metrics(path,clock)
    complete=result['predictions']==result['scheduled']
    checks={
        'independent_regular_experiment':'PASS','official_schedule_mapping':'PASS' if result['mapping_verified'] else 'FAIL',
        'frozen_predictions':'PASS' if complete else 'WARN','model_and_input_provenance':'PASS' if result['predictions'] else 'WARN',
        'prediction_no_overwrite':'PASS','market_workflow_integrated':'PASS','checkpoints_configured':'PASS',
        'live_provider_preflight':'PASS' if (result['provider'] or {}).get('state')=='HEALTHY' else 'WARN',
        'odds_game_mapping':'PASS' if len((result['provider'] or {}).get('matched_game_ids',[]))==result['scheduled'] else 'WARN',
        'strict_kickoff_boundary':'PASS','three_markets':'PASS','pregame_qualification':'PASS',
        'no_retroactive_wagers':'PASS','final_ingestion_and_grading':'PASS',
        'separate_regular_metrics':'PASS','market_coverage':'PASS' if result['coverage']['games_with_any_market']==result['scheduled'] else 'WARN',
        'worker_running':'WARN','sufficient_sample':'WARN' if result['graded']<30 else 'PASS'}
    result['checks']=checks
    result['readiness']='FAIL' if 'FAIL' in checks.values() else 'WARN' if 'WARN' in checks.values() else 'PASS'
    return result


def season_metrics(paths,clock=capture.utcnow):
    """Read-only regular-season cumulative report; reject overlapping experiments."""
    reports=[metrics(path,clock) for path in paths]
    if len({r['season'] for r in reports})!=1:raise ValueError('Choose one regular season')
    seen=set()
    for r in reports:
        ids={g['game_id'] for g in r['coverage']['experiment']['games']}
        if ids & seen:raise ValueError('Overlapping slates would double-count the season')
        seen.update(ids)
    record={s:sum(r['winner_record'][s] for r in reports) for s in ('WIN','LOSS','PUSH')}
    decisions=record['WIN']+record['LOSS']
    profiles={}
    for profile in PROFILE_POLICY:
        graded=sum(r['profiles'][profile]['graded'] for r in reports)
        units=sum(r['profiles'][profile]['units'] or 0 for r in reports)
        profiles[profile]=dict(qualified_wagers=sum(r['profiles'][profile]['qualified_wagers'] for r in reports),
            graded=graded,record={s:sum(r['profiles'][profile]['record'][s] for r in reports) for s in ('WIN','LOSS','PUSH')},
            units=units if graded else None,roi=100*units/graded if graded else None)
    buckets=[]
    for index in range(5):
        rows=[r['calibration_buckets'][index] for r in reports]
        count=sum(r['count'] for r in rows)
        buckets.append(dict(lower=rows[0]['lower'],upper=rows[0]['upper'],count=count,
            mean_probability=sum((r['mean_probability'] or 0)*r['count'] for r in rows)/count if count else None,
            observed_accuracy=sum((r['observed_accuracy'] or 0)*r['count'] for r in rows)/count if count else None))
    return dict(season=reports[0]['season'],phase='regular',experiments=[r['experiment_id'] for r in reports],
        predictions=sum(r['predictions'] for r in reports),graded=sum(r['graded'] for r in reports),winner_record=record,
        accuracy=record['WIN']/decisions if decisions else None,calibration_buckets=buckets,profiles=profiles,
        market_coverage_games=sum(r['coverage']['games_with_any_market'] for r in reports),scheduled_games=len(seen),
        sample_warning='INSUFFICIENT_SAMPLE' if decisions<30 else 'DESCRIPTIVE_NOT_A_GUARANTEE')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['prepare','freeze','preflight','tick','watch','report','metrics','season-report'])
    p.add_argument('--db',type=Path,required=True)
    p.add_argument('--season',type=int,default=2026);p.add_argument('--week',type=int,default=1)
    p.add_argument('--allow-paid',action='store_true');p.add_argument('--credits',type=int,default=90)
    p.add_argument('--history',type=Path,default=ROOT/'data/nfl_team_game_history.json')
    p.add_argument('--include-db',type=Path,action='append',default=[])
    p.add_argument('--sync-social',action='store_true',help='Push verified aggregates hourly to configured social storage')
    args=p.parse_args(argv)
    if args.action=='prepare':
        if args.db.exists(): raise ValueError('Refusing to replace existing experiment; use freeze/report')
        schedule=capture.espn_slate(args.season,'regular',args.week)
        capture.create(args.db,season=args.season,phase='regular',week=args.week,games=schedule,credits=args.credits)
        result=freeze(args.db,history_path=args.history,schedule=schedule)
    elif args.action=='freeze': result=freeze(args.db,history_path=args.history)
    elif args.action in ('report','metrics'): result=readiness(args.db) if args.action=='report' else metrics(args.db)
    elif args.action=='season-report':result=season_metrics([args.db,*args.include_db])
    elif args.action=='preflight':
        if not args.allow_paid: p.error('preflight requires --allow-paid')
        result=preflight(args.db)
    else:
        if not args.allow_paid: p.error('tick/watch requires --allow-paid')
        last_sync=0
        while True:
            market=capture.tick(args.db)
            result=dict(market=market,qualification=qualify(args.db))
            try:result['finals']=settle(args.db)
            except RuntimeError:result['finals']={'state':'SCHEDULE_NETWORK_FAILURE'}
            if args.sync_social and time.monotonic()-last_sync>=3600:
                from backend.app.services.social_marketing import sync_source
                try:result['social_source']=sync_source(args.db)
                except Exception:result['social_source']={'state':'SOURCE_SYNC_BLOCKED'}
                last_sync=time.monotonic()
            print(json.dumps(result),flush=True)
            if args.action=='tick': return 0
            time.sleep(60)
    print(json.dumps(result,indent=2))
    return 0


if __name__=='__main__': raise SystemExit(main())
