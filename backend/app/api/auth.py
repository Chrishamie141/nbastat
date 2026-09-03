import logging
from fastapi import APIRouter, HTTPException, Request, Response
from backend.app.schemas.auth import BootstrapAdminRequest, LoginRequest, PasswordResetConfirmRequest, PasswordResetRequest, RegisterRequest
from backend.app.services.auth_service import authenticate_user, bootstrap_admin, clear_session_cookie, current_user, delete_user_account, register_user, request_password_reset, reset_password, safe_user, set_session_cookie

router = APIRouter(prefix='/api/auth', tags=['auth'])
logger = logging.getLogger(__name__)

def _unavailable(exc: Exception):
    if isinstance(exc, HTTPException):
        raise exc
    logger.exception('Authentication storage is unavailable')
    raise HTTPException(503, 'Account service is temporarily unavailable.') from exc

@router.post('/register')
def register(payload: RegisterRequest, response: Response):
    try: row = register_user(payload.name, payload.email, payload.password)
    except Exception as exc: _unavailable(exc)
    set_session_cookie(response, row['id'], row['session_version'] if 'session_version' in row.keys() else 0)
    return {'user': safe_user(row)}

@router.post('/login')
def login(payload: LoginRequest, response: Response):
    try: row = authenticate_user(payload.email, payload.password)
    except Exception as exc: _unavailable(exc)
    set_session_cookie(response, row['id'], row['session_version'] if 'session_version' in row.keys() else 0)
    return {'user': safe_user(row)}

@router.get('/me')
def me(request: Request): return {'user': safe_user(current_user(request))}

@router.post('/logout')
def logout(response: Response):
    clear_session_cookie(response)
    return {'ok': True}

@router.post('/forgot-password', status_code=202)
def forgot_password(payload: PasswordResetRequest):
    try: request_password_reset(payload.email)
    except Exception as exc: _unavailable(exc)
    return {'message': 'If an account exists, a reset code will be sent.'}

@router.post('/reset-password')
def confirm_password_reset(payload: PasswordResetConfirmRequest, response: Response):
    try: reset_password(payload.email, payload.code, payload.password)
    except Exception as exc: _unavailable(exc)
    clear_session_cookie(response)
    return {'ok': True}

@router.post('/bootstrap-admin')
def initial_admin_setup(payload: BootstrapAdminRequest, response: Response):
    try: row = bootstrap_admin(payload.name, payload.email, payload.password, payload.setup_code)
    except Exception as exc: _unavailable(exc)
    set_session_cookie(response, row['id'], row['session_version'] if 'session_version' in row.keys() else 0)
    return {'user': safe_user(row)}


@router.delete('/account')
def delete_account(payload: LoginRequest, request: Request, response: Response):
    user = current_user(request)
    delete_user_account(int(user['id']), payload.email, payload.password)
    clear_session_cookie(response)
    return {'ok': True}
