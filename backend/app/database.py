"""Database access shared by authentication and billing.

SQLite remains the zero-configuration local default.  Production uses the
PostgreSQL URL supplied by Vercel/Supabase while preserving the small DB-API
surface used by the application (including SQLite-style ``?`` placeholders).
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv
from database_safety import assert_postgres_allowed, assert_sqlite_target

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

_POSTGRES_SCHEMES = ("postgres://", "postgresql://")
_LIBPQ_QUERY_PARAMETERS = {
    "application_name", "channel_binding", "connect_timeout", "dbname",
    "fallback_application_name", "gssencmode", "host", "hostaddr",
    "keepalives", "keepalives_count", "keepalives_idle",
    "keepalives_interval", "krbsrvname", "options", "passfile",
    "password", "port", "replication", "requirepeer", "service",
    "servicefile", "sslcert", "sslcrl", "sslkey", "sslmode",
    "sslpassword", "sslrootcert", "target_session_attrs", "tcp_user_timeout",
    "user",
}


def database_url() -> str:
    # Vercel's Supabase integration provisions POSTGRES_URL.  Keep
    # DATABASE_URL as the explicit override used by local and non-Vercel
    # deployments, but consume the integration value automatically when it
    # is available so production never falls back to ephemeral SQLite.
    candidates = [
        (os.getenv("DATABASE_URL") or "").strip(),
        (os.getenv("POSTGRES_URL") or "").strip(),
        (os.getenv("POSTGRES_URL_NON_POOLING") or "").strip(),
    ]
    if os.getenv("VERCEL"):
        postgres = next((value for value in candidates if value.lower().startswith(_POSTGRES_SCHEMES)), "")
        if not postgres:
            raise RuntimeError("A persistent PostgreSQL database is required on Vercel")
        return postgres
    configured = next((value for value in candidates if value), "")
    if configured:
        return configured
    return "sqlite:///./predictions.db"


def using_postgres() -> bool:
    return database_url().lower().startswith(_POSTGRES_SCHEMES)


def database_path() -> Path:
    url = database_url()
    if url.startswith("sqlite:///"):
        raw = url.replace("sqlite:///", "", 1)
        path = Path(raw)
        return path if path.is_absolute() else BASE_DIR / path
    return BASE_DIR / "predictions.db"


def _postgres_url(url: str) -> str:
    """Remove dashboard-only query fields that libpq cannot parse."""
    parts = urlsplit(url)
    query = urlencode(
        [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
         if key in _LIBPQ_QUERY_PARAMETERS]
    )
    scheme = "postgresql" if parts.scheme == "postgres" else parts.scheme
    return urlunsplit((scheme, parts.netloc, parts.path, query, parts.fragment))


def _postgres_query(query: str) -> str:
    # psycopg treats every percent sign as part of its placeholder grammar.
    # Escape SQL LIKE literals first, then translate the application's
    # SQLite-style placeholders.
    return query.replace("%", "%%").replace("?", "%s")


class PostgresConnection:
    def __init__(self, connection):
        self._connection = connection

    def execute(self, query, params=None):
        normalized = _postgres_query(str(query))
        values = tuple(params) if params is not None else None
        return self._connection.execute(normalized, values)

    def commit(self):
        return self._connection.commit()

    def rollback(self):
        return self._connection.rollback()

    def close(self):
        return self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()
        return False


def get_db_connection():
    if using_postgres():
        assert_postgres_allowed()
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - production dependency
            raise RuntimeError("PostgreSQL support is not installed") from exc
        connection = psycopg.connect(
            _postgres_url(database_url()),
            row_factory=dict_row,
            prepare_threshold=None,
            connect_timeout=5,
        )
        return PostgresConnection(connection)

    target = database_path()
    assert_sqlite_target(target)
    connection = sqlite3.connect(target, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def table_exists(connection, table: str) -> bool:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
        raise ValueError("invalid table name")
    if using_postgres():
        return connection.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=?",
            (table,),
        ).fetchone() is not None
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def column_exists(connection, table: str, column: str) -> bool:
    if using_postgres():
        return connection.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name=? AND column_name=?",
            (table, column),
        ).fetchone() is not None
    return any(row[1] == column for row in connection.execute(f"PRAGMA table_info({table})"))


def initialize_auth_database():
    identifier = "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY" if using_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    with get_db_connection() as connection:
        connection.execute(f"""
            CREATE TABLE IF NOT EXISTS users (
                id {identifier},
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_login_at TEXT,
                is_active INTEGER NOT NULL DEFAULT 1
            )
        """)
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)")
        if using_postgres():
            connection.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_internal INTEGER NOT NULL DEFAULT 0")
            connection.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS session_version INTEGER NOT NULL DEFAULT 0")
        else:
            if not column_exists(connection, "users", "is_internal"):
                connection.execute("ALTER TABLE users ADD COLUMN is_internal INTEGER NOT NULL DEFAULT 0")
            if not column_exists(connection, "users", "session_version"):
                connection.execute("ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used_at TEXT
            )
        """)
        connection.execute("CREATE INDEX IF NOT EXISTS idx_password_reset_user ON password_reset_tokens(user_id)")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS auth_bootstrap (
                setup_key TEXT PRIMARY KEY,
                completed_at TEXT NOT NULL,
                user_id INTEGER
            )
        """)
        if using_postgres():
            # These tables are server-only.  RLS without browser policies plus
            # explicit privilege revocation keeps password-reset and bootstrap
            # state unreachable through Supabase's exposed Data API.
            for table in ("password_reset_tokens", "auth_bootstrap"):
                connection.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
                connection.execute(f"REVOKE ALL ON TABLE {table} FROM anon, authenticated")


def initialize_billing_database():
    initialize_auth_database()
    columns = {
        "stripe_customer_id": "TEXT",
        "stripe_subscription_id": "TEXT",
        "subscription_plan": "TEXT NOT NULL DEFAULT 'none'",
        "subscription_status": "TEXT NOT NULL DEFAULT 'inactive'",
        "subscription_current_period_end": "TEXT",
        "subscription_cancel_at_period_end": "INTEGER NOT NULL DEFAULT 0",
        "subscription_created_at": "TEXT",
        "subscription_updated_at": "TEXT",
        "access_source": "TEXT NOT NULL DEFAULT 'none'",
    }
    with get_db_connection() as connection:
        for name, ddl in columns.items():
            if not column_exists(connection, "users", name):
                connection.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_users_stripe_customer ON users(stripe_customer_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_users_stripe_subscription ON users(stripe_subscription_id)")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS stripe_webhook_events (
                stripe_event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                processed_at TEXT NOT NULL
            )
        """)
