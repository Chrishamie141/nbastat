"""Local Week 1 operator commands. Never creates/freezes an experiment or publishes."""
from contextlib import contextmanager
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / 'backtesting/market_capture/nfl_2026_reg1_v1.db'
RUNTIME = ROOT / '.runtime/week1'
MANIFEST = 'ee7caccb4ab4eb920157b359b89d5cc5cad5af6a6cbd7228cf1f13f0fa1e4261'
PREDICTIONS = 'e57e7e557a19c584dfabc097489bd39b1ea0550180c36f41bc6abe4c8d2591e9'


class AlreadyRunning(RuntimeError):
    """Benign duplicate invocation, distinct from configuration/integrity failure."""


def stamp():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
    os.replace(temporary, path)


@contextmanager
def single_instance(db):
    """OS-held byte lock survives no crash; all watcher entry points share this lock."""
    path = Path(db).resolve().with_suffix('.worker.lock')
    with path.open('a+b') as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            lock = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            unlock = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            lock = lambda: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            unlock = lambda: fcntl.flock(handle, fcntl.LOCK_UN)
        try:
            lock()
        except OSError as exc:
            raise AlreadyRunning('WATCHER_ALREADY_RUNNING') from exc
        try:
            yield
        finally:
            unlock()


def locked(db):
    try:
        with single_instance(db):
            return False
    except AlreadyRunning:
        return True


def preflight(db=DB, *, require_key=True, manifest=MANIFEST, predictions=PREDICTIONS):
    # Set the legacy fallback to the separate capture DB BEFORE importing application code.
    # No legacy writers are used by this workflow; never point an unattended process at production.
    db = Path(db).resolve(strict=True)
    os.environ['DATABASE_URL'] = 'sqlite:///' + db.as_posix()
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    from backtesting import nfl_week_workflow as week
    result = week.metrics(db)
    if (result['experiment_id'] != 'NFL-2026-REG1-v1' or result['predictions'] != 16
            or result['manifest_hash'] != manifest or result['prediction_hash'] != predictions):
        raise ValueError('FROZEN_WEEK1_INTEGRITY_FAILED')
    from backtesting import capture_nfl_game_markets as capture
    with capture.connect(db, readonly=True) as c:
        if c.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('SQLITE_INTEGRITY_FAILED')
    if require_key and not os.getenv('THE_ODDS_API_KEY'):
        raise ValueError('THE_ODDS_API_KEY_MISSING')
    return result


def configure_local(*, create=False, root=ROOT):
    """Local-only signing material, not X credentials; never inherit publication switches."""
    root = Path(root).resolve()
    if 'pytest' in sys.modules and root == ROOT:
        raise ValueError('Tests must use an explicit temporary local configuration')
    path = root / '.runtime/week1/social-local.json'
    if create and not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as f:
            json.dump(dict(signing_key=secrets.token_hex(32),sync_secret=secrets.token_hex(32),campaign_start=stamp()[:10]), f)
    config = json.loads(path.read_text(encoding='utf-8'))
    if create and not config.get('sync_secret'):
        config['sync_secret']=secrets.token_hex(32)
        atomic_json(path,config)
    os.environ.update(SOCIAL_DATABASE_URL='sqlite:///' + (path.parent / 'social-drafts.db').as_posix(),
                      SOCIAL_SOURCE_SIGNING_KEY=config['signing_key'],
                      SOCIAL_CAMPAIGN_START=config['campaign_start'],
                      DRY_RUN='true', SOCIAL_AUTO_PUBLISH='false', SOCIAL_SCHEDULER_ENABLED='false')
    if config.get('sync_secret'):os.environ['SOCIAL_SYNC_SECRET']=config['sync_secret']
    return path.parent


def daily_draft(db=DB):
    configure_local()
    from backend.app.services import social_marketing as social
    social.initialize()
    social.sync_source(db)
    # No publisher call, even if environment variables were changed externally.
    return social.generate()


def status(db=DB, runtime=RUNTIME):
    result = preflight(db, require_key=False)
    from backtesting import capture_nfl_game_markets as capture
    with capture.connect(db, readonly=True) as c:
        next_row = c.execute("SELECT game_id,offset_minutes,due,deadline FROM checkpoints WHERE state='PENDING' AND deadline>? ORDER BY due LIMIT 1", (time.time(),)).fetchone()
        last = c.execute('SELECT MAX(retrieved_at) FROM captures').fetchone()[0]
    try:
        health = json.loads((runtime / 'health.json').read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        health = {}
    running = locked(db)
    age = time.time() - health.get('heartbeat_epoch', 0)
    coverage = result['coverage']
    rows = coverage['checkpoints']
    request_used = [r['used'] for r in coverage['requests'] if r['used'] is not None]
    return dict(worker_running=running, health='HEALTHY' if running and 0 <= age < 300 else 'STALE' if running else 'STOPPED',
                heartbeat=health, next_checkpoint=dict(next_row) if next_row else None,
                next_checkpoint_utc=datetime.fromtimestamp(next_row['due'], timezone.utc).isoformat() if next_row else None,
                completed=coverage['complete_checkpoints'], pending=sum(r['state']=='PENDING' for r in rows),
                missed=sum(r['state'] in ('MISSED_CAPTURE','INTERRUPTED_CAPTURE') for r in rows), alerts=coverage['alerts'],
                provider_status=coverage['last_status'], provider_preflight=result['provider'], quota_state=coverage['quota_state'],
                credits_reserved=coverage['credits_reserved'], credits_limit=coverage['credits_limit'],
                account_credits_used_last_reported=request_used[-1] if request_used else (result['provider'] or {}).get('credits_used'),
                account_remaining=coverage['account_remaining'], latest_successful_capture=last,
                integrity='PASS', manifest_hash=result['manifest_hash'], prediction_hash=result['prediction_hash'],
                qualified_wagers={p:v['qualified_wagers'] for p,v in result['profiles'].items()},
                dry_run=True, auto_publish=False)


def watch(db, *, runtime, local_drafts=False, sync_social=False, tick=None, qualify=None, settle=None,
          clock=time.time, sleep=time.sleep):
    """The lifecycle watch loop, injectable for offline orchestration tests."""
    from backtesting import nfl_week_workflow as week
    from backtesting import capture_nfl_game_markets as capture
    tick, qualify, settle = tick or capture.tick, qualify or week.qualify, settle or week.settle
    runtime = Path(runtime)
    runtime.mkdir(parents=True, exist_ok=True)
    stop = runtime / 'STOP'
    with single_instance(db):
        # A deliberate invocation resumes an earlier graceful stop. Never clear another worker's stop.
        stop.unlink(missing_ok=True)
        health = dict(pid=os.getpid(), started_at=stamp(), state='STARTING', database=str(Path(db).resolve()))
        last_finals, last_social = -float('inf'), -float('inf')
        try:
            while not stop.exists():
                health.update(heartbeat_epoch=clock(), state='RUNNING')
                atomic_json(runtime / 'health.json', health)
                outcome = dict(market=tick(db), qualification=qualify(db))
                # ESPN is free but still avoid calling it before kickoff or every minute.
                with capture.connect(db, readonly=True) as c:
                    first_kickoff = c.execute('SELECT MIN(kickoff_epoch) FROM games').fetchone()[0]
                if clock() >= first_kickoff and clock()-last_finals >= 600:
                    try:
                        outcome['finals'] = settle(db)
                    except RuntimeError:
                        outcome['finals'] = {'state':'SCHEDULE_NETWORK_FAILURE'}
                    last_finals = clock()
                if local_drafts and clock()-last_social >= 3600:
                    try:
                        post = daily_draft(db)
                        health['social'] = dict(state='DRAFT_SAVED', day=post['day_key'], post_id=post['post_id'])
                    except Exception:
                        health['social'] = {'state':'DRAFT_BLOCKED_REVIEW_CONFIG_OR_SOURCE'}
                    last_social = clock()
                elif sync_social and clock()-last_social >= 3600:
                    from backend.app.services.social_marketing import sync_source
                    try:
                        health['social'] = sync_source(db)
                    except Exception:
                        health['social'] = {'state':'SOURCE_SYNC_BLOCKED'}
                    last_social = clock()
                health.update(heartbeat_epoch=clock(), outcome=outcome)
                atomic_json(runtime / 'health.json', health)
                print(json.dumps(outcome), flush=True)
                for _ in range(60):
                    if stop.exists(): break
                    sleep(1)
        except Exception as exc:
            health.update(state='FAILED', error_type=type(exc).__name__)
            raise
        finally:
            if health['state'] != 'FAILED': health['state'] = 'STOPPED'
            health['heartbeat_epoch'] = clock()
            atomic_json(runtime / 'health.json', health)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['setup','run','status','stop'])
    args = p.parse_args(argv)
    if args.action == 'status':
        result = status()
    elif args.action == 'stop':
        RUNTIME.mkdir(parents=True, exist_ok=True)
        (RUNTIME / 'STOP').touch()
        result = {'state':'GRACEFUL_STOP_REQUESTED'}
    else:
        preflight()
        if args.action == 'setup':
            # Locked, exclusive backup of a quiescent SQLite file; never overwrite prior backups.
            with single_instance(DB):
                if any(Path(str(DB)+s).exists() for s in ('-wal','-journal')):
                    raise ValueError('SQLite has active sidecars; stop writers before backup')
                RUNTIME.mkdir(parents=True, exist_ok=True)
                backup = RUNTIME / ('week1-before-worker-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.db')
                before = hashlib.sha256(DB.read_bytes()).hexdigest()
                with DB.open('rb') as source, backup.open('xb') as target:
                    shutil.copyfileobj(source, target)
                if hashlib.sha256(backup.read_bytes()).hexdigest() != before or hashlib.sha256(DB.read_bytes()).hexdigest() != before:
                    raise ValueError('Backup consistency verification failed')
            configure_local(create=True)
            post = daily_draft()
            result = dict(state='PREPARED', backup=str(backup), backup_sha256=before, draft_id=post['post_id'], published=False)
        else:
            if sys.prefix == sys.base_prefix:
                raise ValueError('PROJECT_VIRTUAL_ENVIRONMENT_REQUIRED')
            configure_local()
            from backtesting.nfl_week_workflow import main as workflow
            return workflow(['watch','--db',str(DB),'--allow-paid','--local-drafts','--runtime',str(RUNTIME)])
    print(json.dumps(result, indent=2))
    return 0


def cli(argv=None):
    try:
        return main(argv)
    except AlreadyRunning:
        print(json.dumps({'state':'ALREADY_RUNNING','action':'NO_OP'}))
        return 0
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        # Never log provider URLs/credentials or arbitrary exception messages.
        safe_codes = {'WATCHER_ALREADY_RUNNING','FROZEN_WEEK1_INTEGRITY_FAILED',
                      'SQLITE_INTEGRITY_FAILED','THE_ODDS_API_KEY_MISSING','PROJECT_VIRTUAL_ENVIRONMENT_REQUIRED'}
        reason = str(exc) if str(exc) in safe_codes else type(exc).__name__
        print(json.dumps({'state':'FATAL_CONFIGURATION_OR_INTEGRITY', 'reason':reason}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    # The lifecycle imports this module by canonical name. Use the same exception
    # class there and here (python -m otherwise creates a second __main__ class).
    from backtesting.week1_worker import cli as canonical_cli
    raise SystemExit(canonical_cli())
