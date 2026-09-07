import base64, hashlib, hmac, json, logging, os, secrets, uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse
import requests
from fastapi import HTTPException, Request, Response
from werkzeug.security import check_password_hash, generate_password_hash
from backend.app.database import get_db_connection, initialize_auth_database, table_exists

COOKIE_NAME = 'sbs_session'
SESSION_HOURS = 12
RESET_MINUTES = 30
logger = logging.getLogger(__name__)
CANONICAL_PRODUCTION_ORIGIN = 'https://smartbetsports.com'


class PasswordResetDeliveryUnavailable(RuntimeError):
    """Raised when reset delivery cannot be completed safely."""


def _is_production() -> bool:
    return bool(os.getenv('VERCEL')) or os.getenv('ENVIRONMENT', '').strip().lower() in {'production', 'prod'}

def _now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
def _secret():
    secret = os.getenv('AUTH_SECRET')
    if not secret:
        if _is_production():
            raise RuntimeError('AUTH_SECRET is required in production')
        secret = 'local-development-change-me-only'
    return secret.encode()
def normalize_email(email: str) -> str: return email.strip().lower()
def is_internal_user(row) -> bool:
    # Owner authorization is a durable database role. Environment email lists
    # are intentionally never an authorization source: public registration
    # must not be able to claim an allowlisted address and gain owner access.
    return bool(row['is_internal']) if 'is_internal' in row.keys() else False


def safe_user(row):
    return {'id': row['id'], 'name': row['name'], 'email': row['email'],
            'createdAt': row['created_at'], 'lastLoginAt': row['last_login_at'],
            'isInternal': is_internal_user(row)}


def owner_account_integrity() -> dict:
    """Return non-identifying owner-role integrity for authenticated health views."""
    initialize_auth_database()
    with get_db_connection() as conn:
        row = conn.execute(
            'SELECT COUNT(*) AS owner_count, '
            'SUM(CASE WHEN is_active=1 THEN 1 ELSE 0 END) AS active_owner_count '
            'FROM users WHERE is_internal=1'
        ).fetchone()
    owner_count = int(row['owner_count'] or 0)
    active_owner_count = int(row['active_owner_count'] or 0)
    return {
        'status': 'HEALTHY' if owner_count == 1 and active_owner_count == 1 else 'MISCONFIGURED',
        'ownerCount': owner_count,
        'activeOwnerCount': active_owner_count,
    }
def _b64(data: bytes) -> str: return base64.urlsafe_b64encode(data).decode().rstrip('=')
def _unb64(data: str) -> bytes: return base64.urlsafe_b64decode(data + '=' * (-len(data) % 4))
def create_token(user_id: int, session_version: int = 0) -> str:
    payload = {'sub': user_id, 'sv': session_version, 'exp': int((datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)).timestamp())}
    body = _b64(json.dumps(payload, separators=(',', ':')).encode())
    sig = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return f'{body}.{sig}'
def _verify_token_payload(token: str) -> Optional[dict]:
    try:
        body, sig = token.split('.', 1)
        good = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good): return None
        payload = json.loads(_unb64(body))
        if int(payload['exp']) < int(datetime.now(timezone.utc).timestamp()): return None
        return {'sub': int(payload['sub']), 'sv': int(payload.get('sv', 0))}
    except Exception:
        return None
def verify_token(token: str) -> Optional[int]:
    payload = _verify_token_payload(token)
    return payload['sub'] if payload else None
def set_session_cookie(response: Response, user_id: int, session_version: int = 0):
    secure = _is_production() or os.getenv('AUTH_COOKIE_SECURE', 'false').lower() == 'true'
    response.set_cookie(COOKIE_NAME, create_token(user_id, session_version), httponly=True, secure=secure, samesite='lax', max_age=SESSION_HOURS*3600, path='/')
def clear_session_cookie(response: Response): response.delete_cookie(COOKIE_NAME, path='/')
def register_user(name: str, email: str, password: str):
    initialize_auth_database(); email = normalize_email(email); now = _now(); hashed = generate_password_hash(password, method="scrypt")
    try:
        with get_db_connection() as conn:
            inserted = conn.execute(
                'INSERT INTO users(name,email,password_hash,created_at,updated_at,is_active) VALUES(?,?,?,?,?,1) RETURNING id',
                (name.strip(), email, hashed, now, now),
            ).fetchone()
            conn.commit(); row = conn.execute('SELECT * FROM users WHERE id=?', (inserted['id'],)).fetchone()
            return row
    except Exception as exc:
        if 'UNIQUE' in str(exc).upper(): raise HTTPException(status_code=409, detail='Email is already registered.')
        raise
def authenticate_user(email: str, password: str):
    initialize_auth_database(); email = normalize_email(email)
    with get_db_connection() as conn:
        row = conn.execute('SELECT * FROM users WHERE email=? AND is_active=1', (email,)).fetchone()
        if not row or not check_password_hash(row['password_hash'], password):
            raise HTTPException(status_code=401, detail='Invalid email or password.')
        now = _now(); conn.execute('UPDATE users SET last_login_at=?, updated_at=? WHERE id=?', (now, now, row['id'])); conn.commit()
        return conn.execute('SELECT * FROM users WHERE id=?', (row['id'],)).fetchone()
def current_user(request: Request):
    token = request.cookies.get(COOKIE_NAME); payload = _verify_token_payload(token) if token else None
    if not payload: raise HTTPException(status_code=401, detail='Your session has expired. Please log in again.')
    with get_db_connection() as conn:
        row = conn.execute('SELECT * FROM users WHERE id=? AND is_active=1', (payload['sub'],)).fetchone()
    if not row or int(row['session_version'] if 'session_version' in row.keys() else 0) != payload['sv']:
        raise HTTPException(status_code=401, detail='Your session has expired. Please log in again.')
    return row

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

def _site_origin() -> str:
    configured = os.getenv('SITE_URL', '').strip()
    if not configured:
        configured = CANONICAL_PRODUCTION_ORIGIN if _is_production() else os.getenv('FRONTEND_ORIGIN', 'http://localhost:3000')
    parsed = urlparse(configured)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'', '/'}:
        raise PasswordResetDeliveryUnavailable('SITE_URL must be an origin without credentials, path, query, or fragment')
    origin = f'{parsed.scheme}://{parsed.netloc}'.rstrip('/')
    if _is_production() and origin != CANONICAL_PRODUCTION_ORIGIN:
        raise PasswordResetDeliveryUnavailable('SITE_URL must match the canonical production origin')
    return origin


def _reset_delivery_configured() -> bool:
    return bool(os.getenv('RESEND_API_KEY') and os.getenv('PASSWORD_RESET_FROM_EMAIL'))


def ensure_password_reset_delivery_available() -> None:
    if not _reset_delivery_configured():
        raise PasswordResetDeliveryUnavailable('Password reset delivery is not configured')
    _site_origin()


def _send_reset_email(email: str, code: str) -> bool:
    api_key = os.getenv('RESEND_API_KEY')
    sender = os.getenv('PASSWORD_RESET_FROM_EMAIL')
    if not api_key or not sender:
        raise PasswordResetDeliveryUnavailable('Password reset delivery is not configured')
    reset_url = f'{_site_origin()}/reset-password'
    payload = {
        'from': sender,
        'to': [email],
        'subject': 'Your SmartBetSports password reset code',
        'text': f'Reset your SmartBetSports password at:\n\n{reset_url}\n\nEnter this one-time code:\n\n{code}\n\nIt expires in {RESET_MINUTES} minutes. If you did not request this, ignore this email.',
    }
    try:
        response = requests.post(
            'https://api.resend.com/emails',
            json=payload,
            headers={'Authorization': f'Bearer {api_key}'},
            timeout=10,
        )
        if 200 <= response.status_code < 300:
            return True
        logger.error('Password reset provider rejected delivery with status %s', response.status_code)
        return False
    except requests.RequestException:
        logger.exception('Password reset email delivery failed')
        return False

def request_password_reset(email: str) -> None:
    # Check provider configuration before account lookup so known and unknown
    # addresses have indistinguishable externally observable failure behavior.
    ensure_password_reset_delivery_available()
    initialize_auth_database(); email = normalize_email(email); now = datetime.now(timezone.utc).replace(microsecond=0)
    with get_db_connection() as conn:
        row = conn.execute('SELECT id FROM users WHERE email=? AND is_active=1', (email,)).fetchone()
        if not row:
            return
        recent = conn.execute(
            'SELECT created_at FROM password_reset_tokens WHERE user_id=? AND used_at IS NULL ORDER BY created_at DESC LIMIT 1',
            (row['id'],),
        ).fetchone()
        if recent and datetime.fromisoformat(recent['created_at']) > now - timedelta(seconds=60):
            return
        code = secrets.token_urlsafe(24)
        conn.execute('UPDATE password_reset_tokens SET used_at=? WHERE user_id=? AND used_at IS NULL', (now.isoformat(), row['id']))
        conn.execute(
            'INSERT INTO password_reset_tokens(id,user_id,token_hash,created_at,expires_at) VALUES(?,?,?,?,?)',
            (str(uuid.uuid4()), row['id'], _token_hash(code), now.isoformat(), (now + timedelta(minutes=RESET_MINUTES)).isoformat()),
        )
    if not _send_reset_email(email, code):
        with get_db_connection() as conn:
            conn.execute('UPDATE password_reset_tokens SET used_at=? WHERE token_hash=?', (_now(), _token_hash(code)))
        # Keep the public response indistinguishable from an unknown account.
        # Delivery failure is operationally logged and the unusable token is
        # revoked; configured-provider outages must not become an account oracle.
        logger.error('Password reset email delivery failed after a send attempt')

def reset_password(email: str, code: str, password: str) -> None:
    initialize_auth_database(); email = normalize_email(email); now = _now()
    with get_db_connection() as conn:
        claimed = conn.execute(
            'UPDATE password_reset_tokens SET used_at=? WHERE token_hash=? AND used_at IS NULL AND expires_at>? '
            'AND user_id=(SELECT id FROM users WHERE email=? AND is_active=1) RETURNING user_id',
            (now, _token_hash(code.strip()), now, email),
        ).fetchone()
        if not claimed:
            raise HTTPException(400, 'The reset code is invalid or expired.')
        conn.execute('UPDATE users SET password_hash=?,session_version=session_version+1,updated_at=? WHERE id=?',
                     (generate_password_hash(password, method='scrypt'), now, claimed['user_id']))

def bootstrap_admin(name: str, email: str, password: str, setup_code: str):
    expected = os.getenv('INITIAL_ADMIN_SETUP_TOKEN', '')
    if len(expected) < 32 or not hmac.compare_digest(setup_code, expected):
        raise HTTPException(401, 'Unable to complete initial setup.')
    initialize_auth_database(); email = normalize_email(email); now = _now()
    with get_db_connection() as conn:
        if conn.execute('SELECT 1 FROM users WHERE is_internal=1 LIMIT 1').fetchone():
            raise HTTPException(409, 'Initial setup has already been completed.')
        claimed = conn.execute(
            "INSERT INTO auth_bootstrap(setup_key,completed_at) VALUES('initial_admin',?) "
            "ON CONFLICT(setup_key) DO NOTHING RETURNING setup_key", (now,),
        ).fetchone()
        if not claimed:
            raise HTTPException(409, 'Initial setup has already been completed.')
        existing = conn.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone()
        if existing:
            conn.execute('UPDATE users SET name=?,password_hash=?,is_active=1,is_internal=1,session_version=session_version+1,updated_at=? WHERE id=?',
                         (name.strip(), generate_password_hash(password, method='scrypt'), now, existing['id']))
            user_id = existing['id']
        else:
            inserted = conn.execute(
                'INSERT INTO users(name,email,password_hash,created_at,updated_at,is_active,is_internal) VALUES(?,?,?,?,?,1,1) RETURNING id',
                (name.strip(), email, generate_password_hash(password, method='scrypt'), now, now),
            ).fetchone(); user_id = inserted['id']
        conn.execute("UPDATE auth_bootstrap SET user_id=? WHERE setup_key='initial_admin'", (user_id,))
        return conn.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()


def delete_user_account(user_id: int, email: str, password: str) -> None:
    """Delete one authenticated account and only its account-owned records."""
    verified = authenticate_user(email, password)
    if int(verified["id"]) != int(user_id):
        raise HTTPException(status_code=403, detail="Account confirmation does not match the active session.")
    owned_tables = (
        "fantasy_depth_charts", "nfl_game_predictions", "parlay_history",
        "predictions", "graded_bets",
    )
    with get_db_connection() as connection:
        for table in owned_tables:
            if table_exists(connection, table):
                connection.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
        deleted = connection.execute(
            "DELETE FROM users WHERE id=? AND lower(email)=?", (user_id, normalize_email(email))
        )
        if deleted.rowcount != 1:
            raise HTTPException(status_code=404, detail="Account not found.")
