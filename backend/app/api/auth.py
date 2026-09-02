from fastapi import APIRouter, Request, Response
from backend.app.schemas.auth import RegisterRequest, LoginRequest
from backend.app.services.auth_service import authenticate_user, clear_session_cookie, current_user, delete_user_account, register_user, safe_user, set_session_cookie

router = APIRouter(prefix='/api/auth', tags=['auth'])

@router.post('/register')
def register(payload: RegisterRequest, response: Response):
    row = register_user(payload.name, payload.email, payload.password)
    set_session_cookie(response, row['id'])
    return {'user': safe_user(row)}

@router.post('/login')
def login(payload: LoginRequest, response: Response):
    row = authenticate_user(payload.email, payload.password)
    set_session_cookie(response, row['id'])
    return {'user': safe_user(row)}

@router.get('/me')
def me(request: Request): return {'user': safe_user(current_user(request))}

@router.post('/logout')
def logout(response: Response):
    clear_session_cookie(response)
    return {'ok': True}


@router.delete('/account')
def delete_account(payload: LoginRequest, request: Request, response: Response):
    user = current_user(request)
    delete_user_account(int(user['id']), payload.email, payload.password)
    clear_session_cookie(response)
    return {'ok': True}
