from backend.app.database import database_path_for_url, database_url, is_postgres_url, using_postgres


def test_database_url_uses_vercel_supabase_integration(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_URL", "postgresql://example.invalid/postgres")

    assert database_url() == "postgresql://example.invalid/postgres"
    assert using_postgres() is True


def test_database_url_prefers_explicit_override(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./local.db")
    monkeypatch.setenv("POSTGRES_URL", "postgresql://example.invalid/postgres")

    assert database_url() == "sqlite:///./local.db"
    assert using_postgres() is False


def test_explicit_database_helpers_do_not_read_global_configuration(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://broad.example/app")
    isolated = tmp_path / "pipeline.db"

    assert is_postgres_url("postgresql://scoped.example/postgres") is True
    assert is_postgres_url(f"sqlite:///{isolated.as_posix()}") is False
    assert database_path_for_url(f"sqlite:///{isolated.as_posix()}") == isolated
