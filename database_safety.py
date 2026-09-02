"""Fail-closed database boundaries for tests; no production routing changes.

The pytest bootstrap configures disposable roots before collecting application
modules. Application connectors call these guards even if fixtures are absent.
The bootstrap also installs a SQLite audit hook to cover direct/cached connects.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import sys
from urllib.parse import parse_qs, unquote, urlsplit

PRODUCTION_DATABASE = Path(__file__).resolve().parent / "predictions.db"
_test_roots: tuple[Path, ...] = ()
_sqlite_connect = sqlite3.connect


class UnsafeTestDatabaseError(RuntimeError):
    """A test attempted to escape its explicitly isolated database storage."""


def isolated_tests() -> bool:
    return "pytest" in sys.modules or os.environ.get("SMARTBETS_TEST_ISOLATION") == "1"


def allow_test_root(root: Path) -> None:
    global _test_roots
    root = root.resolve()
    if root == PRODUCTION_DATABASE.parent or root in PRODUCTION_DATABASE.parents:
        raise UnsafeTestDatabaseError("The source workspace cannot be a test database root")
    _test_roots = tuple(dict.fromkeys((*_test_roots, root)))


def replace_test_roots(*roots: Path) -> tuple[Path, ...]:
    """Limit the active test to its own roots; return roots for teardown restore."""
    global _test_roots
    previous = _test_roots
    _test_roots = ()
    try:
        for root in roots:
            allow_test_root(root)
    except BaseException:
        _test_roots = previous
        raise
    return previous


def assert_sqlite_target(database) -> None:
    if not isolated_tests():
        return
    raw = os.fsdecode(database)
    if raw in ("", ":memory:"):
        return  # SQLite-managed temporary/in-memory databases cannot touch disk targets.
    if raw.startswith("file:"):
        parts = urlsplit(raw)
        query = parse_qs(parts.query)
        if parts.path == ":memory:" or query.get("mode") == ["memory"]:
            return
        if parts.netloc not in ("", "localhost"):
            raise UnsafeTestDatabaseError("Network SQLite paths are forbidden in tests")
        raw = unquote(parts.path)
        if os.name == "nt" and len(raw) > 3 and raw[0] == "/" and raw[2] == ":":
            raw = raw[1:]
    target = Path(raw).resolve()
    production = PRODUCTION_DATABASE.resolve()
    if target == production or (target.exists() and production.exists() and target.samefile(production)):
        raise UnsafeTestDatabaseError("pytest cannot access the production predictions.db")
    if not any(target.is_relative_to(root) for root in _test_roots):
        raise UnsafeTestDatabaseError("SQLite test database must be inside a registered temporary root")


def assert_postgres_allowed() -> None:
    if isolated_tests():
        # No live-PostgreSQL test configuration is supported by this suite.
        # URL/adapter unit tests may inspect configuration or use a fake driver.
        raise UnsafeTestDatabaseError("PostgreSQL connections are forbidden during isolated tests")


def sqlite_audit_guard(event: str, args: tuple) -> None:
    if event == "sqlite3.connect":
        assert_sqlite_target(args[0])


def guarded_sqlite_connect(*args, **kwargs):
    """Install ATTACH protection after SQLite has initialized its handle."""
    connection = _sqlite_connect(*args, **kwargs)

    def authorize(action, first, _second, _database, _trigger):
        if action == sqlite3.SQLITE_ATTACH:
            try:
                assert_sqlite_target(first)
            except (UnsafeTestDatabaseError, TypeError, ValueError):
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(authorize)
    return connection
