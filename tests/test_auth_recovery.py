from __future__ import annotations

import pytest
from fastapi import HTTPException, Response


class CookieRequest:
    def __init__(self, token):
        self.cookies = {"sbs_session": token}


def test_password_reset_is_one_time_hashed_and_revokes_existing_sessions(monkeypatch):
    from backend.app.database import get_db_connection
    from backend.app.services import auth_service

    account = auth_service.register_user("Chris", "owner@example.com", "old-password")
    old_token = auth_service.create_token(account["id"], account["session_version"])
    delivered = {}
    monkeypatch.setattr(auth_service, "_send_reset_email", lambda email, code: delivered.update(email=email, code=code) or True)

    auth_service.request_password_reset("OWNER@example.com")
    assert delivered["email"] == "owner@example.com"
    with get_db_connection() as connection:
        stored = connection.execute("SELECT token_hash FROM password_reset_tokens").fetchone()["token_hash"]
    assert delivered["code"] not in stored

    auth_service.reset_password("owner@example.com", delivered["code"], "new-password")
    assert auth_service.authenticate_user("owner@example.com", "new-password")
    with pytest.raises(HTTPException) as stale:
        auth_service.current_user(CookieRequest(old_token))
    assert stale.value.status_code == 401
    with pytest.raises(HTTPException) as reused:
        auth_service.reset_password("owner@example.com", delivered["code"], "another-password")
    assert reused.value.status_code == 400


def test_password_reset_request_does_not_disclose_unknown_accounts(monkeypatch):
    from backend.app.services import auth_service

    sent = []
    monkeypatch.setattr(auth_service, "_send_reset_email", lambda email, code: sent.append((email, code)) or True)
    assert auth_service.request_password_reset("missing@example.com") is None
    assert sent == []


def test_initial_admin_bootstrap_requires_secret_and_can_run_only_once(monkeypatch):
    from backend.app.services import auth_service

    setup_code = "setup-secret-that-is-at-least-thirty-two-characters"
    monkeypatch.setenv("INITIAL_ADMIN_SETUP_TOKEN", setup_code)
    with pytest.raises(HTTPException) as denied:
        auth_service.bootstrap_admin("Chris", "owner@example.com", "strong-password", "x" * 32)
    assert denied.value.status_code == 401

    owner = auth_service.bootstrap_admin("Chris", "owner@example.com", "strong-password", setup_code)
    assert owner["is_internal"] == 1
    with pytest.raises(HTTPException) as repeated:
        auth_service.bootstrap_admin("Other", "other@example.com", "strong-password", setup_code)
    assert repeated.value.status_code == 409


def test_vercel_database_configuration_fails_closed(monkeypatch):
    from backend.app.database import database_url

    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL_NON_POOLING", raising=False)
    with pytest.raises(RuntimeError, match="persistent PostgreSQL"):
        database_url()

    monkeypatch.setenv("DATABASE_URL", "sqlite:///./predictions.db")
    monkeypatch.setenv("POSTGRES_URL", "postgresql://database.example/app")
    assert database_url() == "postgresql://database.example/app"
    monkeypatch.setenv("DATABASE_URL", "   ")
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    with pytest.raises(RuntimeError, match="persistent PostgreSQL"):
        database_url()


def test_auth_database_outage_is_normalized_to_503(monkeypatch):
    from backend.app.api import auth
    from backend.app.schemas.auth import RegisterRequest

    monkeypatch.setattr(auth, "register_user", lambda *_: (_ for _ in ()).throw(OSError("private database path")))
    with pytest.raises(HTTPException) as unavailable:
        auth.register(RegisterRequest(name="Chris", email="owner@example.com", password="strong-password"), Response())
    assert unavailable.value.status_code == 503
    assert unavailable.value.detail == "Account service is temporarily unavailable."
    assert "private database path" not in unavailable.value.detail


def test_registration_insert_outage_reaches_503_boundary(monkeypatch):
    from contextlib import contextmanager
    from backend.app.api import auth
    from backend.app.schemas.auth import RegisterRequest
    from backend.app.services import auth_service

    class BrokenConnection:
        def execute(self, *_args, **_kwargs):
            raise OSError("private insert failure")

    @contextmanager
    def broken_connection():
        yield BrokenConnection()

    monkeypatch.setattr(auth_service, "initialize_auth_database", lambda: None)
    monkeypatch.setattr(auth_service, "get_db_connection", broken_connection)
    with pytest.raises(HTTPException) as unavailable:
        auth.register(RegisterRequest(name="Chris", email="owner@example.com", password="strong-password"), Response())
    assert unavailable.value.status_code == 503
