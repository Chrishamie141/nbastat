"""Suite-wide isolation, installed BEFORE test module imports/collection.

Never load application modules above this bootstrap. Temporary roots and the
audit hook deliberately remain active through session teardown. No root .env or
production database is copied. Each test receives a fresh, schema-initialized DB.
"""
from __future__ import annotations

import gc
import os
from pathlib import Path
import sys
import sqlite3
import tempfile

import pytest

from database_safety import allow_test_root, assert_postgres_allowed, guarded_sqlite_connect, replace_test_roots, sqlite_audit_guard

_collection_root = Path(tempfile.mkdtemp(prefix="smartbets-pytest-collection-"))
allow_test_root(_collection_root)
os.environ["SMARTBETS_TEST_ISOLATION"] = "1"
os.environ["DATABASE_URL"] = f"sqlite:///{(_collection_root / 'collection.db').as_posix()}"
os.environ["SMARTBETS_TEST_SQLITE_PATH"] = str(_collection_root / "collection.db")
# Empty values prevent dotenv from repopulating real provider/database credentials.
for _key in ("POSTGRES_URL", "POSTGRES_URL_NON_POOLING", "THE_ODDS_API_KEY", "ODDS_API_KEY",
             "OPENWEATHER_API_KEY", "OPEN_WEATHER_API_KEY", "INTERNAL_ADMIN_EMAILS"):
    os.environ[_key] = ""
os.environ["AUTH_SECRET"] = "isolated-pytest-secret-not-for-production"
os.environ["STRIPE_SECRET_KEY"] = "sk_test_placeholder"
os.environ["STRIPE_WEBHOOK_SECRET"] = "whsec_test"
os.environ["STRIPE_FOUNDING_MONTHLY_PRICE_ID"] = "price_founder"
sys.addaudithook(sqlite_audit_guard)
sqlite3.connect = guarded_sqlite_connect
sqlite3.dbapi2.connect = guarded_sqlite_connect
try:
    import psycopg
except ImportError:
    pass
else:
    def _reject_postgres(*args, **kwargs):
        assert_postgres_allowed()
    psycopg.connect = _reject_postgres
    psycopg.Connection.connect = _reject_postgres
    psycopg.AsyncConnection.connect = _reject_postgres


@pytest.fixture(autouse=True)
def isolated_test_database(tmp_path, tmp_path_factory, monkeypatch):
    """Redirect both web and legacy storage, without any captured default paths."""
    # Sibling directory: cache-cleaning tests may recursively erase tmp_path.
    database_root = tmp_path_factory.mktemp("smartbets-test-db")
    previous_roots = replace_test_roots(tmp_path, database_root)
    database = database_root / "isolated-test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database.as_posix()}")
    monkeypatch.setenv("SMARTBETS_TEST_SQLITE_PATH", str(database))
    monkeypatch.setenv("POSTGRES_URL", "")
    monkeypatch.setenv("POSTGRES_URL_NON_POOLING", "")
    # TemporaryDirectory-based backtesting stores stay inside this test's root.
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    from backend.app.database import initialize_billing_database
    from prediction_storage import ensure_user_columns, initialize_database
    try:
        initialize_billing_database()
        initialize_database()
        ensure_user_columns()
        yield database
    finally:
        gc.collect()  # release SQLite context-manager handles before Windows cleanup
        replace_test_roots(*previous_roots)
