from backend.app.database import database_url, using_postgres


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
