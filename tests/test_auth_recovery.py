from __future__ import annotations

import pytest
from fastapi import BackgroundTasks, HTTPException, Response


class CookieRequest:
    def __init__(self, token):
        self.cookies = {"sbs_session": token}


def test_password_reset_is_one_time_hashed_and_revokes_existing_sessions(monkeypatch):
    from backend.app.database import get_db_connection
    from backend.app.services import auth_service

    account = auth_service.register_user("Chris", "owner@example.com", "old-password")
    old_token = auth_service.create_token(account["id"], account["session_version"])
    delivered = {}
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    monkeypatch.setenv("PASSWORD_RESET_FROM_EMAIL", "security@example.com")
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
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    monkeypatch.setenv("PASSWORD_RESET_FROM_EMAIL", "security@example.com")
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


def test_email_allowlist_cannot_grant_owner_access(monkeypatch):
    from backend.app.services import auth_service

    monkeypatch.setenv("INTERNAL_ADMIN_EMAILS", "attacker@example.com")
    account = auth_service.register_user("Attacker", "attacker@example.com", "strong-password")
    assert auth_service.is_internal_user(account) is False
    assert auth_service.safe_user(account)["isInternal"] is False


def test_owner_login_rejects_normal_account_without_setting_cookie():
    from backend.app.api import auth
    from backend.app.schemas.auth import LoginRequest
    from backend.app.services import auth_service

    auth_service.register_user("Member", "member@example.com", "strong-password")
    response = Response()
    with pytest.raises(HTTPException) as denied:
        auth.owner_login(LoginRequest(email="member@example.com", password="strong-password"), response)
    assert denied.value.status_code == 401
    assert response.headers.get("set-cookie") is None


def test_owner_integrity_reports_exactly_one_active_database_owner(monkeypatch):
    from backend.app.services import auth_service

    setup_code = "owner-integrity-setup-token-that-is-long-enough"
    monkeypatch.setenv("INITIAL_ADMIN_SETUP_TOKEN", setup_code)
    assert auth_service.owner_account_integrity()["status"] == "MISCONFIGURED"
    auth_service.bootstrap_admin("Owner", "owner@example.com", "strong-password", setup_code)
    assert auth_service.owner_account_integrity() == {
        "status": "HEALTHY", "ownerCount": 1, "activeOwnerCount": 1,
    }


def test_password_reset_fails_closed_when_delivery_is_unconfigured(monkeypatch):
    from backend.app.api import auth
    from backend.app.schemas.auth import PasswordResetRequest

    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("PASSWORD_RESET_FROM_EMAIL", raising=False)
    with pytest.raises(HTTPException) as unavailable:
        auth.forgot_password(PasswordResetRequest(email="anyone@example.com"), BackgroundTasks())
    assert unavailable.value.status_code == 503
    assert unavailable.value.detail == "Password reset is temporarily unavailable. Please try again."


def test_production_reset_origin_is_canonical(monkeypatch):
    from backend.app.services import auth_service

    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("SITE_URL", "https://wrong.example")
    with pytest.raises(auth_service.PasswordResetDeliveryUnavailable, match="canonical"):
        auth_service._site_origin()
    monkeypatch.setenv("SITE_URL", "https://smartbetsports.com")
    assert auth_service._site_origin() == "https://smartbetsports.com"


def test_provider_failure_does_not_reveal_known_account(monkeypatch):
    from backend.app.api import auth
    from backend.app.schemas.auth import PasswordResetRequest
    from backend.app.services import auth_service

    monkeypatch.setenv("RESEND_API_KEY", "configured-test-key")
    monkeypatch.setenv("PASSWORD_RESET_FROM_EMAIL", "security@example.com")
    monkeypatch.setattr(auth_service, "_send_reset_email", lambda *_: False)
    auth_service.register_user("Known", "known@example.com", "strong-password")

    known_tasks = BackgroundTasks()
    unknown_tasks = BackgroundTasks()
    known = auth.forgot_password(PasswordResetRequest(email="known@example.com"), known_tasks)
    unknown = auth.forgot_password(PasswordResetRequest(email="unknown@example.com"), unknown_tasks)
    assert known == unknown == {"message": "If an account exists, a reset code will be sent."}
    assert len(known_tasks.tasks) == len(unknown_tasks.tasks) == 1
