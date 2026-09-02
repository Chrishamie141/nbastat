from __future__ import annotations

import pytest
from fastapi import HTTPException


def test_account_deletion_requires_matching_credentials_and_removes_owned_rows(monkeypatch, tmp_path):
    from backend.app.database import get_db_connection
    from backend.app.services.auth_service import delete_user_account, register_user

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'accounts.db').as_posix()}")
    account = register_user("Audit", "audit@example.com", "strong-password")
    with get_db_connection() as connection:
        connection.execute(
            "CREATE TABLE predictions (id INTEGER PRIMARY KEY, user_id INTEGER, value TEXT)"
        )
        connection.execute(
            "INSERT INTO predictions(user_id,value) VALUES(?,?)", (account["id"], "owned")
        )
        connection.execute(
            "INSERT INTO users(name,email,password_hash,created_at,updated_at,is_active) "
            "VALUES('Other','other@example.com','x','now','now',1)"
        )

    with pytest.raises(HTTPException) as error:
        delete_user_account(int(account["id"]), "audit@example.com", "wrong-password")
    assert error.value.status_code == 401

    delete_user_account(int(account["id"]), "audit@example.com", "strong-password")
    with get_db_connection() as connection:
        assert connection.execute(
            "SELECT 1 FROM users WHERE id=?", (account["id"],)
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM predictions WHERE user_id=?", (account["id"],)
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM users WHERE email='other@example.com'"
        ).fetchone() is not None
