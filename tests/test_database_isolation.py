"""Real writes in disposable stores; production bytes are only read/hashed."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from database_safety import PRODUCTION_DATABASE, UnsafeTestDatabaseError

ROOT = Path(__file__).resolve().parents[1]
# This executes during collection, before the per-test fixture.
from prediction_storage import initialize_database
initialize_database()
COLLECTION_DATABASE = os.environ["SMARTBETS_TEST_SQLITE_PATH"]


def _production_digest():
    return hashlib.sha256(PRODUCTION_DATABASE.read_bytes()).hexdigest() if PRODUCTION_DATABASE.exists() else None


@pytest.mark.parametrize("iteration", range(2))
def test_each_test_starts_with_a_fresh_database(iteration, isolated_test_database):
    from backend.app.database import database_path, get_db_connection
    from prediction_storage import get_connection

    assert database_path() == isolated_test_database
    with get_db_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        connection.execute("CREATE TABLE isolation_marker(value INTEGER)")
        connection.execute("INSERT INTO isolation_marker VALUES (?)", (iteration,))
    with get_connection() as connection:
        assert connection.execute("SELECT value FROM isolation_marker").fetchone()[0] == iteration


@pytest.mark.parametrize("target", [PRODUCTION_DATABASE, "predictions.db", PRODUCTION_DATABASE.as_uri(),
                                    PRODUCTION_DATABASE.as_uri() + "?mode=ro"])
def test_raw_sqlite_rejects_default_database(target):
    before = _production_digest()
    with pytest.raises(UnsafeTestDatabaseError):
        sqlite3.connect(target, uri=str(target).startswith("file:"))
    assert _production_digest() == before


def test_application_connectors_fail_closed(monkeypatch, tmp_path):
    from backend.app.database import get_db_connection
    from prediction_storage import get_connection
    from backtesting.prediction_store import PredictionStore

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{PRODUCTION_DATABASE.as_posix()}")
    for connect in (get_db_connection, lambda: get_connection(PRODUCTION_DATABASE),
                    lambda: PredictionStore(PRODUCTION_DATABASE)):
        with pytest.raises(UnsafeTestDatabaseError):
            connect()
    # Missing isolation defaults must fail, not silently route to production.
    monkeypatch.delenv("SMARTBETS_TEST_SQLITE_PATH")
    with pytest.raises(UnsafeTestDatabaseError):
        get_connection()
    outside = tmp_path.parent / "not-this-tests-database.db"
    with pytest.raises(UnsafeTestDatabaseError):
        sqlite3.connect(outside)
    with pytest.raises(UnsafeTestDatabaseError):
        sqlite3.connect(COLLECTION_DATABASE)


def test_postgres_is_rejected_before_opening_a_connection(monkeypatch):
    from backend.app.database import get_db_connection

    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/production")
    with pytest.raises(UnsafeTestDatabaseError, match="PostgreSQL"):
        get_db_connection()


def test_raw_postgres_driver_is_rejected():
    psycopg = pytest.importorskip("psycopg")
    with pytest.raises(UnsafeTestDatabaseError, match="PostgreSQL"):
        psycopg.connect("postgresql://example.invalid/production")
    with pytest.raises(UnsafeTestDatabaseError, match="PostgreSQL"):
        psycopg.Connection.connect("postgresql://example.invalid/production")


def test_attach_cannot_bypass_sqlite_connection_guard(isolated_test_database):
    with sqlite3.connect(isolated_test_database) as connection:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("ATTACH DATABASE ? AS production", (str(PRODUCTION_DATABASE),))


def test_alias_to_production_is_rejected_without_touching_real_database(tmp_path, monkeypatch):
    import database_safety

    sentinel = tmp_path / "protected.db"
    sentinel.write_bytes(b"protected database sentinel")
    alias = tmp_path / "alias.db"
    try:
        alias.hardlink_to(sentinel)
    except OSError:
        pytest.skip("Hard links unavailable on this filesystem")
    monkeypatch.setattr(database_safety, "PRODUCTION_DATABASE", sentinel)
    with pytest.raises(UnsafeTestDatabaseError):
        sqlite3.connect(alias)
    assert sentinel.read_bytes() == b"protected database sentinel"


def test_db_writing_workflow_preserves_production_bytes(isolated_test_database):
    from backend.app.services.auth_service import register_user
    from backend.app.services.nfl_product_service import _save_prediction, save_depth_chart
    from backend.app.database import get_db_connection

    before = _production_digest()
    user = register_user("Isolation", "isolation@example.invalid", "isolated-password")
    chart = save_depth_chart(int(user["id"]), "Isolated lineup", {})
    _save_prediction(0, {"game_id": "isolation-only", "season": 2030, "week": 1, "status": "scheduled",
                        "season_type": "regular", "kickoff_time": "2030-09-01T23:00:00Z"},
                     {"modelVersion": "test-only", "winner": "BUF", "winProbability": .6})
    with get_db_connection() as connection:
        assert chart["id"] > 0
        assert connection.execute("SELECT COUNT(*) FROM nfl_game_predictions").fetchone()[0] == 1
    assert _production_digest() == before


def test_collection_imports_are_isolated_even_with_production_environment(tmp_path):
    project = tmp_path / "collection-check"
    project.mkdir()
    (project / "conftest.py").write_text((ROOT / "conftest.py").read_text(encoding="utf-8"), encoding="utf-8")
    (project / "test_collect.py").write_text(
        "from backend.app.database import initialize_auth_database\n"
        "from prediction_storage import initialize_database\n"
        "initialize_auth_database()\ninitialize_database()\n"
        "def test_collected(): pass\n", encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(ROOT), DATABASE_URL=f"sqlite:///{PRODUCTION_DATABASE.as_posix()}")
    before = _production_digest()
    result = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q"],
                            cwd=project, env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 test collected" in result.stdout
    assert _production_digest() == before


def test_production_guard_is_inert_outside_pytest():
    # No database is opened; this verifies production behavior has no test routing.
    env = {key: value for key, value in os.environ.items()
           if key not in {"SMARTBETS_TEST_ISOLATION", "PYTEST_CURRENT_TEST"}}
    result = subprocess.run([sys.executable, "-c", "from database_safety import *; "
                             "assert not isolated_tests(); assert_sqlite_target(PRODUCTION_DATABASE); "
                             "assert_postgres_allowed()"], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
