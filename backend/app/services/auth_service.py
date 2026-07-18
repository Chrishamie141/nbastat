import base64, hashlib, hmac, json, os
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import HTTPException, Request, Response
from werkzeug.security import check_password_hash, generate_password_hash
from backend.app.database import get_db_connection, initialize_auth_database

COOKIE_NAME = 'sbs_session'
SESSION_HOURS = 12

def _now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
def _secret():
    secret = os.getenv('AUTH_SECRET')
    if not secret:
        secret = 'local-development-change-me-only'
    return secret.encode()
def normalize_email(email: str) -> str: return email.strip().lower()
def safe_user(row):
    return {'id': row['id'], 'name': row['name'], 'email': row['email'], 'createdAt': row['created_at'], 'lastLoginAt': row['last_login_at']}
def _b64(data: bytes) -> str: return base64.urlsafe_b64encode(data).decode().rstrip('=')
def _unb64(data: str) -> bytes: return base64.urlsafe_b64decode(data + '=' * (-len(data) % 4))
def create_token(user_id: int) -> str:
    payload = {'sub': user_id, 'exp': int((datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)).timestamp())}
    body = _b64(json.dumps(payload, separators=(',', ':')).encode())
    sig = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
    return f'{body}.{sig}'
def verify_token(token: str) -> Optional[int]:
    try:
        body, sig = token.split('.', 1)
        good = _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, good): return None
        payload = json.loads(_unb64(body))
        if int(payload['exp']) < int(datetime.now(timezone.utc).timestamp()): return None
        return int(payload['sub'])
    except Exception:
        return None
def set_session_cookie(response: Response, user_id: int):
    secure = os.getenv('AUTH_COOKIE_SECURE', 'false').lower() == 'true'
    response.set_cookie(COOKIE_NAME, create_token(user_id), httponly=True, secure=secure, samesite='lax', max_age=SESSION_HOURS*3600, path='/')
def clear_session_cookie(response: Response): response.delete_cookie(COOKIE_NAME, path='/')
def register_user(name: str, email: str, password: str):
    initialize_auth_database(); email = normalize_email(email); now = _now(); hashed = generate_password_hash(password, method="scrypt")
    try:
        with get_db_connection() as conn:
            cur = conn.execute('INSERT INTO users(name,email,password_hash,created_at,updated_at,is_active) VALUES(?,?,?,?,?,1)', (name.strip(), email, hashed, now, now))
            conn.commit(); row = conn.execute('SELECT * FROM users WHERE id=?', (cur.lastrowid,)).fetchone()
            return row
    except Exception as exc:
        if 'UNIQUE' in str(exc).upper(): raise HTTPException(status_code=409, detail='Email is already registered.')
        raise HTTPException(status_code=400, detail='Unable to create account.')
def authenticate_user(email: str, password: str):
    initialize_auth_database(); email = normalize_email(email)
    with get_db_connection() as conn:
        row = conn.execute('SELECT * FROM users WHERE email=? AND is_active=1', (email,)).fetchone()
        if not row or not check_password_hash(row['password_hash'], password):
            raise HTTPException(status_code=401, detail='Invalid email or password.')
        now = _now(); conn.execute('UPDATE users SET last_login_at=?, updated_at=? WHERE id=?', (now, now, row['id'])); conn.commit()
        return conn.execute('SELECT * FROM users WHERE id=?', (row['id'],)).fetchone()
def current_user(request: Request):
    token = request.cookies.get(COOKIE_NAME); user_id = verify_token(token) if token else None
    if not user_id: raise HTTPException(status_code=401, detail='Your session has expired. Please log in again.')
    with get_db_connection() as conn:
        row = conn.execute('SELECT * FROM users WHERE id=? AND is_active=1', (user_id,)).fetchone()
    if not row: raise HTTPException(status_code=401, detail='Your session has expired. Please log in again.')
    return row
